"""WAVE4 evaluation and compliance boundaries keep existing numeric results."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from types import SimpleNamespace

from benchmark.accounts.config import AccountExtensionConfig, mirror_legacy_account_columns
from benchmark.application.compliance.rules import RuleCatalog
from benchmark.application.evaluation.checkpoint import CheckpointService
from benchmark.application.evaluation.service import EvaluateTraceRequest, EvaluationService
from benchmark.application.evaluation.tool_schema import resolve_tool_schema
from benchmark.extensions import ExtensionSettings, build_extension_runtime
from benchmark.persistence import SqlAlchemyUnitOfWork
from database.connection import Base
from database.models import Account, AgentPeriodCheckpoint, AgentTrace, AIDecisionLog, RuntimeEvent, User
from services.evaluation.data_loader import EvaluationDataLoader
from benchmark.application.compliance.service import ComplianceRequest, ComplianceService
from benchmark.providers.llm import LLMResponse
from services.evaluation.checkpoint_service import create_checkpoint_if_due
from services.evaluation.llm_tool_judge import LLMToolJudgeEvaluator
from services.evaluation_api_service import EvaluationApiService


def test_checkpoint_service_rerun_does_not_add_rows_and_keeps_formula():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    seed = factory()
    user = User(username="wave4-checkpoint")
    seed.add(user)
    seed.flush()
    account = Account(
        user_id=user.id,
        name="wave4",
        account_type="AI",
        is_active="true",
        initial_capital=10000,
        current_cash=10000,
        frozen_cash=0,
    )
    seed.add(account)
    seed.commit()
    account_id = account.id
    seed.close()

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    now = datetime(2026, 6, 1, 15, 30, tzinfo=timezone.utc)
    service = CheckpointService(session_scope=scope)
    first = service.run_due((3600, 3600, 0), now)
    second = service.run_due((3600,), now)
    assert first.created == 1
    assert first.intervals == (3600,)
    assert second.created == 0

    check = factory()
    rows = check.query(AgentPeriodCheckpoint).filter_by(account_id=account_id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.period_end == datetime(2026, 6, 1, 15, 0)
    assert float(row.equity_start) == 10000
    assert float(row.equity_end) == 10000
    assert float(row.pnl) == 0
    assert row.return_rate == 0.0
    assert row.volatility == 0.0
    direct = create_checkpoint_if_due(
        check,
        check.query(Account).filter_by(id=account_id).one(),
        3600,
        now,
    )
    assert direct is None
    check.close()
    engine.dispose()


def test_repository_checkpoint_view_matches_stored_numbers(db_session):
    user = User(username="wave4-view")
    db_session.add(user)
    db_session.flush()
    account = Account(
        user_id=user.id,
        name="agent",
        initial_capital=10000,
        current_cash=10100,
        frozen_cash=0,
    )
    db_session.add(account)
    db_session.flush()
    end = datetime(2026, 6, 1, 15, 0)
    db_session.add(
        AgentPeriodCheckpoint(
            account_id=account.id,
            interval_seconds=3600,
            period_start=end - timedelta(hours=1),
            period_end=end,
            equity_start=10000,
            equity_end=10100,
            pnl=100,
            return_rate=0.01,
            volatility=0.02,
        )
    )
    db_session.flush()

    service = EvaluationApiService(db_session)
    item = service.list_account_checkpoints(account.id, 3600, 10)["items"][0]
    board = service.leaderboard(3600, None, 10, "pnl")["items"][0]
    compared = service.compare_agents(3600, None, None, 10)["items"][0]
    assert item["pnl"] == 100.0
    assert item["return_rate"] == 0.01
    assert item["volatility"] == 0.02
    assert board["pnl"] == 100.0
    assert board["agent_name"] == "agent"
    assert compared["return_rate"] == 0.01
    assert compared["pnl"] == 100.0


def test_missing_tool_version_is_unavailable_and_current_version_is_not_substituted():
    runtime = build_extension_runtime(ExtensionSettings())
    installed = runtime.tools.get("core.execute_trade").extension.version
    missing = resolve_tool_schema(runtime, "core.execute_trade", None)
    unknown = resolve_tool_schema(runtime, "core.execute_trade", "unknown")
    other = resolve_tool_schema(runtime, "core.execute_trade", "9.9.9")
    absent = resolve_tool_schema(runtime, "core.not_a_tool", installed)
    matched = resolve_tool_schema(runtime, "core.execute_trade", installed)

    assert missing["status"] == "unavailable"
    assert missing["schema"] is None
    assert unknown["status"] == "unavailable"
    assert other["status"] == "unavailable"
    assert other["reason"] == "version_not_installed"
    assert absent["status"] == "unavailable"
    assert matched["status"] == "available"
    assert matched["version"] == installed
    assert matched["schema"]["type"] == "object"


def test_evaluate_trace_leaves_missing_identity_null():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    seed = factory()
    user = User(username="wave4-trace")
    seed.add(user)
    seed.flush()
    account = Account(
        user_id=user.id,
        name="trace-agent",
        initial_capital=10000,
        current_cash=10000,
        frozen_cash=0,
    )
    seed.add(account)
    seed.flush()
    seed.add(
        AgentTrace(
            trace_id="trace-wave4",
            account_id=account.id,
            step_number=1,
            role="assistant",
            tool_calls='[{"function": {"name": "core.execute_trade", "arguments": "{}"}}]',
        )
    )
    seed.add(
        RuntimeEvent(
            id="event-wave4",
            account_id=account.id,
            decision_round_id="round-wave4",
            trace_id="trace-wave4",
            event_type="run.configured",
            sequence=1,
            payload='{"component_versions": {"core.react": "1.0.0"}}',
        )
    )
    seed.commit()
    account_id = account.id
    seed.close()

    runtime = build_extension_runtime(ExtensionSettings())

    def uow_factory():
        return SqlAlchemyUnitOfWork(factory)

    result = EvaluationService(uow_factory, runtime).evaluate_trace(
        EvaluateTraceRequest(account_id=account_id, trace_id="trace-wave4")
    )
    dumped = result.model_dump()
    tool = dumped["tools"][0]
    schema = tool.get("schema_", tool.get("schema"))
    assert dumped["account_id"] == account_id
    assert dumped["account_name"] == "trace-agent"
    assert dumped["decision_round_id"] == "round-wave4"
    assert dumped["component_versions"] == {"core.react": "1.0.0"}
    assert tool["status"] == "unavailable"
    assert schema is None
    assert "api_key" not in str(dumped)

    empty = EvaluationService(uow_factory, runtime).evaluate_trace(
        EvaluateTraceRequest(account_id=account_id, trace_id="missing-trace")
    )
    assert empty.trace_id is None
    assert empty.decision_round_id is None
    assert empty.component_versions == {}
    assert empty.tools == []
    engine.dispose()


def test_runtime_save_mirror_updates_scheduler_columns():
    account = SimpleNamespace(
        agent_type="react",
        enable_rule_aware="false",
        memory_enabled="false",
        tool_routing_enabled="true",
    )
    mirror_legacy_account_columns(
        account,
        AccountExtensionConfig(
            agent_id="baseline.buy-hold",
            agent_config={},
        ),
    )
    assert account.agent_type == "buy_hold"
    assert account.enable_rule_aware == "false"

    mirror_legacy_account_columns(
        account,
        AccountExtensionConfig(
            agent_id="core.rule-aware",
            agent_config={"memory_enabled": True, "tool_routing_enabled": False},
        ),
    )
    assert account.agent_type == "rule_aware"
    assert account.enable_rule_aware == "true"
    assert account.memory_enabled == "true"
    assert account.tool_routing_enabled == "false"


def test_llm_judge_uses_the_port():
    seen = []

    class Port:
        model = "fake-judge"

        def complete(self, request):
            seen.append(request)
            return LLMResponse(content='{"overall_score": 1, "reason": "ok"}')

    evaluator = LLMToolJudgeEvaluator(system_prompt="score the trace", llm_port=Port(), model="fake-judge")
    result = evaluator.evaluate(
        {"trace": {"trace_id": "t", "steps": []}, "account_info": {"account_name": "a"}},
        {},
    )
    assert seen[0].model == "fake-judge"
    assert result["token_usage"]["model"] == "fake-judge"
    assert "api_key" not in str(result["judge_parsed"])


def test_legacy_tool_name_uses_recorded_public_version():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    seed = factory()
    user = User(username="wave4-alias")
    seed.add(user)
    seed.flush()
    account = Account(
        user_id=user.id,
        name="alias-agent",
        initial_capital=10000,
        current_cash=10000,
        frozen_cash=0,
    )
    seed.add(account)
    seed.flush()
    runtime = build_extension_runtime(ExtensionSettings())
    installed = runtime.tools.get("core.execute_trade").extension.version
    seed.add(
        AgentTrace(
            trace_id="trace-alias",
            account_id=account.id,
            step_number=1,
            role="assistant",
            tool_calls='[{"function": {"name": "execute_trade", "arguments": "{}"}}]',
        )
    )
    seed.add(
        RuntimeEvent(
            id="event-alias",
            account_id=account.id,
            decision_round_id="round-alias",
            trace_id="trace-alias",
            event_type="tool.completed",
            sequence=1,
            payload='{"tool_name": "core.execute_trade", "tool_version": "%s"}' % installed,
        )
    )
    seed.commit()
    account_id = account.id
    seed.close()

    def uow_factory():
        return SqlAlchemyUnitOfWork(factory)

    result = EvaluationService(uow_factory, runtime).evaluate_trace(
        EvaluateTraceRequest(account_id=account_id, trace_id="trace-alias")
    )
    tool = result.tools[0]
    assert tool.requested_name == "execute_trade"
    assert tool.name == "core.execute_trade"
    assert tool.status == "available"
    assert tool.version == installed
    assert tool.schema_ is not None
    engine.dispose()


def test_loader_time_filter_accepts_aware_bounds(db_session):
    user = User(username="wave4-time")
    db_session.add(user)
    db_session.flush()
    account = Account(
        user_id=user.id,
        name="time-agent",
        initial_capital=10000,
        current_cash=10000,
        frozen_cash=0,
    )
    db_session.add(account)
    db_session.flush()
    inside = datetime(2026, 6, 1, 12, 0)
    outside = datetime(2026, 5, 1, 12, 0)
    db_session.add_all(
        [
            AIDecisionLog(
                account_id=account.id,
                decision_time=inside,
                reason="inside",
                operation="hold",
                prev_portion=0,
                target_portion=0,
                total_balance=10000,
                executed="false",
            ),
            AIDecisionLog(
                account_id=account.id,
                decision_time=outside,
                reason="outside",
                operation="hold",
                prev_portion=0,
                target_portion=0,
                total_balance=10000,
                executed="false",
            ),
        ]
    )
    db_session.flush()
    start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc)
    rows = EvaluationDataLoader(db_session).get_decisions(account.id, start, end)
    assert [row.reason for row in rows] == ["inside"]


def test_compliance_evaluate_leaves_missing_scores_null(db_session):
    user = User(username="wave4-compliance")
    db_session.add(user)
    db_session.flush()
    account = Account(
        user_id=user.id,
        name="compliance-agent",
        initial_capital=10000,
        current_cash=10000,
        frozen_cash=0,
    )
    db_session.add(account)
    db_session.flush()
    result = ComplianceService(db_session).evaluate(ComplianceRequest(account_id=account.id, trace_id="missing"))
    assert result.account_id == account.id
    assert result.trace_id == "missing"
    assert result.decision_round_id is None
    assert result.component_versions == {}
    assert result.gate_pass is None
    assert result.final_score is None
    assert "api_key" not in result.model_dump()


def test_rule_catalog_reads_json_without_prompt_modules():
    import benchmark.application.compliance.rules as rules_module

    text = open(rules_module.__file__, encoding="utf-8").read()
    assert "services.agent" not in text
    assert "benchmark.prompts" not in text
    assert "benchmark.builtin.prompts" not in text
    summary = RuleCatalog().summary()
    listed = RuleCatalog().list_rules()
    assert summary["total_rules"] == listed["total"]
    assert summary["total_rules"] == summary["r0_count"] + summary["r1_count"] + summary["r2_count"]
    assert summary["r0_count"] > 0


def test_risk_metrics_keep_fixed_snapshot_results(db_session):
    """Freeze nonzero risk values through the real SQLite snapshot read path."""
    from database.models import AccountSnapshot
    from services.evaluation.risk_evaluator import RiskEvaluator

    user = User(username="wave4-risk")
    db_session.add(user)
    db_session.flush()
    account = Account(
        user_id=user.id, name="risk-agent", initial_capital=10000,
        current_cash=12000, frozen_cash=0,
    )
    db_session.add(account)
    db_session.flush()
    start = datetime(2026, 6, 1)
    for index, equity in enumerate([10000, 11000, 10000, 9000, 8000, 12000]):
        db_session.add(AccountSnapshot(
            account_id=account.id, ts=start + timedelta(hours=index),
            total_equity=equity, cash=equity, positions_value=0,
        ))
    db_session.flush()

    evaluator = RiskEvaluator(db_session)
    result = evaluator.evaluate({"account_id": account.id})
    assert result["total_checkpoints"] == 5
    assert result["drawdown_events"] == {
        "count": 1, "max_drawdown": -0.2727,
        "events": [{
            "peak_time": "2026-06-01T01:00:00", "trough_time": "2026-06-01T04:00:00",
            "drawdown_pct": -0.2727, "peak_equity": 11000.0, "trough_equity": 8000.0,
        }],
    }
    assert result["sharp_movements"]["sharp_losses"]["count"] == 3
    assert result["sharp_movements"]["sharp_gains"]["count"] == 2
    assert result["loss_streaks"]["max_streak_length"] == 3
    assert result["loss_streaks"]["events"][0]["total_loss"] == -0.302
    assert result["tail_risk"] == {
        "threshold": -0.1089, "count": 1, "avg_tail_loss": -0.1111,
        "events": [{"time": "2026-06-01T04:00:00", "return_rate": -0.1111}],
    }
    bounded = evaluator.evaluate({
        "account_id": account.id, "start_time": start + timedelta(hours=2),
        "end_time": start + timedelta(hours=4),
    })
    assert bounded["total_checkpoints"] == 2
    assert bounded["sharp_movements"]["sharp_gains"]["count"] == 0
