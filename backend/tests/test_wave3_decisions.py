import threading
import time
from types import SimpleNamespace
from decimal import Decimal

import pytest

from benchmark.application.decisions.service import (
    DecisionRoundService,
    RunDecisionRound,
)
from benchmark.application.decisions.observability import redact
from benchmark.contracts import TerminationReason


def test_round_filters_and_limits_concurrency_and_reports_errors():
    active = 0
    peak = 0
    guard = threading.Lock()
    ids = []

    def worker(account_id, prices, round_id, **kwargs):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
            ids.append(account_id)
        time.sleep(0.02)
        with guard:
            active -= 1
        if account_id == 3:
            raise ValueError("failure")
        return SimpleNamespace(termination_reason=TerminationReason.HOLD)

    service = DecisionRoundService(
        selector=lambda selected: list(selected),
        prices=lambda: {"BTC": 60000},
        worker=worker,
        is_cancelled=lambda: False,
        lock=threading.Lock(),
    )
    result = service.run(RunDecisionRound((1, 2, 3), 2, "test"))
    assert set(ids) == {1, 2, 3}
    assert peak == 2
    assert result.processed_accounts == 2
    assert result.errors == {3: "ValueError"}
    assert result.decision_round_id


def test_round_overlap_and_lock_release():
    lock = threading.Lock()
    service = DecisionRoundService(
        selector=lambda _: [], lock=lock, is_cancelled=lambda: False
    )
    lock.acquire()
    assert service.run(RunDecisionRound(None, 1, "test")).decision_round_id is None
    lock.release()
    assert service.run(RunDecisionRound(None, 1, "test")).decision_round_id
    assert not lock.locked()


def test_trading_context_identity_and_replay():
    from benchmark.extensions.host import get_extension_runtime
    from benchmark.tools import SynchronousToolInvoker
    from benchmark.builtin.agents._legacy_ports import InvokerBackedToolRegistry
    from benchmark.contracts import KNOWN_CAPABILITIES

    runtime = get_extension_runtime()
    entry = runtime.tools.get("core.execute_trade")
    calls = []
    old = entry.tool._handler

    def execute(context, arguments):
        calls.append((context.account_id, context.decision_round_id, context.call_id))
        return {"executed": False}

    entry.tool._handler = execute
    try:
        invoker = SynchronousToolInvoker(
            runtime.tools,
            account_id=7,
            decision_round_id="round",
            trace_id="trace",
            capabilities=frozenset(KNOWN_CAPABILITIES),
        )
        legacy = InvokerBackedToolRegistry(invoker)
        for _ in range(2):
            legacy.get("execute_trade")(
                operation="hold",
                tool_call_id="same-call",
                decision_round_id="forged-round",
            )
        from benchmark.builtin.agents.rule_aware import _PublicToolBridge

        rule_bridge = _PublicToolBridge(invoker, [])
        for _ in range(2):
            rule_bridge.get("execute_trade")(
                operation="hold", tool_call_id="same-call", decision_round_id="ignored"
            )
        assert calls == [(7, "round", "same-call")] * 4
        assert not invoker.call(
            "core.execute_trade", {"operation": "hold", "account_id": 8}
        ).ok
    finally:
        entry.tool._handler = old


def test_redaction_nested_and_serialized():
    value = {
        "api_key": "secret",
        "content": "Bearer abc123",
        "output": '{"password":"abc", "url":"https://user:pass@host/?token=abc"}',
        "text": "key-is-abc123",
    }
    result = str(redact(value, ("abc123",)))
    assert "abc123" not in result
    assert "user:pass" not in result
    assert '"abc"' not in result


def test_worker_snapshot_releases_connection(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from database.connection import Base
    from database.models import Account, User
    from benchmark.application.decisions.context_builder import load_worker_input

    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        user = User(username="test", password_hash="test")
        db.add(user)
        db.flush()
        account = Account(
            user_id=user.id,
            name="test",
            initial_capital=10000,
            current_cash=10000,
            frozen_cash=0,
            margin_used=0,
            model="gpt-5.6-luna",
            api_key="test-key",
        )
        db.add(account)
        db.commit()
        aid = account.id
    input = load_worker_input(aid, {"BTC": 60000}, "round", session_factory=sessions)
    assert input.context.portfolio.total_assets == Decimal("10000")
    assert len(input.context.trace_id) == 36
    assert "test-key" not in repr(input)
    assert engine.pool.checkedout() == 0


def test_failed_runtime_event_is_discoverable(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from database.connection import Base
    from database.models import Account, User
    from benchmark.application.decisions.observability import PersistentEventSink
    from services.agent_api_service import AgentApiService

    engine = create_engine(f"sqlite:///{tmp_path / 'events.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        user = User(username="test", password_hash="test")
        db.add(user)
        db.flush()
        account = Account(
            user_id=user.id, name="test", initial_capital=10000, current_cash=10000
        )
        db.add(account)
        db.commit()
        aid = account.id
    context = SimpleNamespace(
        account_id=aid, trace_id="failed-trace", decision_round_id="round"
    )
    sink = PersistentEventSink(context, session_factory=sessions)
    sink.record("run.failed", {"error_type": "ValueError"})
    with sessions() as db:
        service = AgentApiService(db)
        assert service.get_latest_trace(aid) == {"trace_id": "failed-trace"}
        assert service.get_trace_history(aid, 10)[0]["trace_id"] == "failed-trace"
        assert service.get_trace("failed-trace")["events"][0]["type"] == "run.failed"


@pytest.mark.parametrize(
    "agent_id,response",
    [
        ("core.react", "Stay flat. <TRADE_DONE>"),
        (
            "core.multi-agent",
            '{"next_action":"finish","final_decision":{"operation":"hold","reason":"stay flat"}}',
        ),
        (
            "core.advanced-multi-agent",
            '{"next_action":"finish","reason":"stay flat","execution_plan":[],"execution_summary":"hold"}',
        ),
        ("core.rule-aware", "Stay flat. <TRADE_DONE>"),
    ],
)
def test_worker_runs_real_builtin_runtime_with_short_sessions(
    tmp_path, monkeypatch, agent_id, response
):
    from functools import partial
    from dataclasses import replace
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from database.connection import Base
    from database.models import Account, User, RuntimeEvent
    from benchmark.application.decisions import runner
    from benchmark.application.decisions.context_builder import load_worker_input
    from benchmark.accounts.config import AccountExtensionConfig
    from tests.fakes import FakeLLM, FakeLLMResponse

    engine = create_engine(f"sqlite:///{tmp_path / 'worker.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        user = User(username="worker", password_hash="test")
        db.add(user)
        db.flush()
        account = Account(
            user_id=user.id,
            name="worker",
            model="fake-model",
            api_key="test-key",
            initial_capital=10000,
            current_cash=10000,
            margin_used=0,
            frozen_cash=0,
        )
        db.add(account)
        db.commit()
        aid = account.id
    runtime = runner.get_extension_runtime()
    config = AccountExtensionConfig(
        agent_id,
        {"max_steps": 2},
        disabled_tools=tuple(spec.name for spec in runtime.tools.list()),
        prompt_profile_id=agent_id + ".default",
    )
    original = load_worker_input(aid, {"BTC": 60000}, "round", session_factory=sessions)
    monkeypatch.setattr(
        runner, "load_worker_input", lambda *args: replace(original, config=config)
    )
    monkeypatch.setattr(
        runner,
        "PersistentEventSink",
        partial(runner.PersistentEventSink, session_factory=sessions),
    )
    monkeypatch.setattr(runner, "SessionLocal", sessions)

    class CheckedLLM(FakeLLM):
        def call(self, *args, **kwargs):
            assert engine.pool.checkedout() == 0
            return super().call(*args, **kwargs)

    fake = CheckedLLM([FakeLLMResponse(response) for _ in range(8)])
    monkeypatch.setattr(runner, "LLMClient", lambda **kwargs: fake)
    from services.agent.multi_agent_advanced import AdvancedMultiAgent

    monkeypatch.setattr(AdvancedMultiAgent, "_notify_evaluator", lambda *args: None)
    result = runner.run_account(aid, {"BTC": 60000}, "round")
    assert result.trace_id
    assert fake.closed
    assert fake.calls
    assert engine.pool.checkedout() == 0
    with sessions() as db:
        types = {row.event_type for row in db.query(RuntimeEvent).all()}
        assert {
            "agent.started",
            "agent.completed",
            "prompt.rendered",
            "llm.completed",
            "run.result",
        } <= types


@pytest.mark.parametrize("value", ["omitted", None, False, True])
@pytest.mark.parametrize("account_enabled", [False, True])
def test_worker_memory_tool_visibility(monkeypatch, value, account_enabled):
    from unittest.mock import Mock
    from benchmark.application.decisions import runner
    from benchmark.accounts.config import AccountExtensionConfig
    from benchmark.testing.context import build_fake_context
    from tests.fakes import FakeLLM

    runtime = runner.get_extension_runtime()
    memory = {"core.memory_add", "core.memory_search"}
    config = AccountExtensionConfig(
        "core.react",
        {} if value == "omitted" else {"memory_enabled": value},
        disabled_tools=tuple(
            spec.name for spec in runtime.tools.list() if spec.name not in memory
        ),
    )
    worker = SimpleNamespace(
        context=build_fake_context(),
        config=config,
        model="fake",
        api_key="test-key",
        base_url=None,
        memory_enabled=account_enabled,
        tool_routing_enabled=False,
    )
    visible = []

    class Captured(Exception):
        pass

    class CaptureRuntime:
        def __init__(self, registry, build):
            visible.extend(spec.name for spec in build.tools.list_specs())

        def run(self, *args, **kwargs):
            raise Captured()

    monkeypatch.setattr(runner, "load_worker_input", lambda *args: worker)
    monkeypatch.setattr(runner, "AgentRuntime", CaptureRuntime)
    monkeypatch.setattr(runner, "LLMClient", lambda **kwargs: FakeLLM([]))
    events = SimpleNamespace(record=Mock(), emit=Mock())
    with pytest.raises(Captured):
        runner._run_account(1, {"BTC": 100}, "round", events, "trace", lambda: False)
    expected = account_enabled if value in ("omitted", None) else value
    assert set(visible) == (memory if expected else set())


@pytest.mark.parametrize("fail", [False, True])
def test_rule_aware_closes_audit_client_on_every_exit(fail):
    from unittest.mock import Mock
    from benchmark.builtin.agents.rule_aware import RuleAwareAgentAdapter
    from benchmark.testing.context import build_fake_context
    from benchmark.testing.events import FakeEventSink
    from services.agent.rule_aware.rule_aware_agent import RuleAwareAgent

    legacy = object.__new__(RuleAwareAgent)
    audit_client = Mock()
    legacy.llm_auditor = SimpleNamespace(llm_client=audit_client)
    legacy.run = (
        Mock(side_effect=RuntimeError("injected failure"))
        if fail
        else Mock(return_value={"termination_reason": "hold", "reason": "stay flat"})
    )
    adapter = RuleAwareAgentAdapter(legacy, events=FakeEventSink())
    if fail:
        with pytest.raises(RuntimeError, match="injected failure"):
            adapter.run(build_fake_context())
    else:
        assert (
            adapter.run(build_fake_context()).termination_reason
            == TerminationReason.HOLD
        )
    audit_client.close.assert_called_once_with()


def test_llm_retry_stops_when_cancelled(monkeypatch):
    from services.agent.llm_client import LLMClient
    from unittest.mock import Mock

    client = object.__new__(LLMClient)
    client.model = "fake"
    client.max_retries = 1
    cancelled = False

    def request(**kwargs):
        nonlocal cancelled
        cancelled = True
        raise RuntimeError("transient")

    create = Mock(side_effect=request)
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    client.is_cancelled = lambda: cancelled
    monkeypatch.setattr("services.agent.llm_client.time.sleep", lambda _: None)
    with pytest.raises(TimeoutError, match="cancelled"):
        client._create_with_retry(
            {"timeout": 5}, call_id="test", message_count=1, tool_count=0
        )
    assert create.call_count == 1


def test_round_reports_step_exhaustion_as_incomplete():
    service = DecisionRoundService(
        selector=lambda selected:list(selected), prices=lambda:{'BTC':100},
        worker=lambda *args,**kwargs:SimpleNamespace(termination_reason=TerminationReason.MAX_STEPS),
        is_cancelled=lambda:False, lock=threading.Lock(),
    )
    result=service.run(RunDecisionRound((1,),1,'test'))
    assert result.processed_accounts==1
    assert result.errors=={1:'max_steps'}
