from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import get_ident

import pytest

from benchmark.agents import (
    AgentDescriptor,
    AgentRegistry,
    AgentRuntime,
    AgentSelection,
    AgentRuntimeError,
    ComponentConfigError,
)
from benchmark.contracts import AgentRunResult, TerminationReason
from tests.fakes import AwaitableAgent


class Factory:
    def __init__(self, agent_builder):
        self.agent_builder = agent_builder
        self.configs = []

    def create(self, context, config):
        self.configs.append(config)
        return self.agent_builder(config)


class HoldAgent:
    def __init__(self, config, thread_ids=None):
        self.config = config
        self.thread_ids = thread_ids

    def run(self, context):
        if self.thread_ids is not None:
            self.thread_ids.append(get_ident())
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=TerminationReason.HOLD,
            summary=f"steps={self.config['max_steps']}",
        )


def registry_for(factory):
    registry = AgentRegistry()
    registry.register(
        AgentDescriptor(
            "com.example.sync-agent",
            "1.0.0",
            {
                "type": "object",
                "properties": {
                    "max_steps": {"type": "integer", "minimum": 1, "default": 3}
                },
                "additionalProperties": False,
            },
        ),
        factory,
    )
    registry.freeze()
    return registry


def test_third_party_agent_runs_in_caller_thread_and_emits_lifecycle(
    build_context, decision_context, event_sink
):
    thread_ids = []
    factory = Factory(lambda config: HoldAgent(config, thread_ids))
    runtime = AgentRuntime(registry_for(factory), build_context)
    caller_thread = get_ident()

    result = runtime.run(AgentSelection("com.example.sync-agent"), decision_context)

    assert result.termination_reason is TerminationReason.HOLD
    assert result.summary == "steps=3"
    assert thread_ids == [caller_thread]
    assert dict(factory.configs[0]) == {"max_steps": 3}
    assert [event.type for event in event_sink.events] == ["agent.started", "agent.completed"]


def test_runtime_rejects_awaitable_without_running_an_event_loop(build_context, decision_context):
    runtime = AgentRuntime(registry_for(Factory(lambda config: AwaitableAgent())), build_context)

    with pytest.raises(AgentRuntimeError) as caught:
        runtime.run(AgentSelection("com.example.sync-agent"), decision_context)

    assert caught.value.code == "ASYNC_AGENT_UNSUPPORTED"


@pytest.mark.parametrize(("field", "value"), [("trace_id", "wrong"), ("decision_round_id", "wrong")])
def test_runtime_rejects_result_context_mismatch(build_context, decision_context, field, value):
    class WrongContextAgent:
        def run(self, context):
            values = {
                "trace_id": context.trace_id,
                "decision_round_id": context.decision_round_id,
            }
            values[field] = value
            return AgentRunResult(
                **values,
                termination_reason=TerminationReason.HOLD,
            )

    runtime = AgentRuntime(registry_for(Factory(lambda config: WrongContextAgent())), build_context)
    with pytest.raises(AgentRuntimeError) as caught:
        runtime.run(AgentSelection("com.example.sync-agent"), decision_context)
    assert caught.value.code == "AGENT_CONTEXT_MISMATCH"


def test_runtime_rejects_bad_config_before_factory(build_context, decision_context):
    factory = Factory(lambda config: HoldAgent(config))
    runtime = AgentRuntime(registry_for(factory), build_context)

    with pytest.raises(ComponentConfigError) as caught:
        runtime.run(
            AgentSelection("com.example.sync-agent", config={"max_steps": 0}),
            decision_context,
        )

    assert caught.value.code == "AGENT_CONFIG_INVALID"
    assert factory.configs == []


def test_runtime_checks_cancellation_and_deadline_before_invocation(
    build_context, decision_context, event_sink
):
    factory = Factory(lambda config: HoldAgent(config))
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    runtime = AgentRuntime(registry_for(factory), build_context, clock=lambda: now)

    cancelled = runtime.run(
        AgentSelection("com.example.sync-agent"),
        decision_context,
        is_cancelled=lambda: True,
    )
    assert cancelled.termination_reason is TerminationReason.CANCELLED
    assert factory.configs == []
    assert event_sink.events[-1].type == "agent.cancelled"

    with pytest.raises(AgentRuntimeError) as caught:
        runtime.run(
            AgentSelection("com.example.sync-agent"),
            decision_context,
            deadline_at=now - timedelta(seconds=1),
        )
    assert caught.value.code == "AGENT_DEADLINE_EXCEEDED"
    assert factory.configs == []


def test_runtime_wraps_extension_errors_but_not_process_exceptions(
    build_context, decision_context
):
    class BrokenAgent:
        def run(self, context):
            raise RuntimeError("provider secret must not leak")

    runtime = AgentRuntime(registry_for(Factory(lambda config: BrokenAgent())), build_context)
    with pytest.raises(AgentRuntimeError) as caught:
        runtime.run(AgentSelection("com.example.sync-agent"), decision_context)
    assert caught.value.code == "AGENT_RUNTIME_ERROR"
    assert "provider secret" not in str(caught.value)
    assert caught.value.__cause__.__class__ is RuntimeError

    class InterruptingAgent:
        def run(self, context):
            raise KeyboardInterrupt()

    runtime = AgentRuntime(registry_for(Factory(lambda config: InterruptingAgent())), build_context)
    with pytest.raises(KeyboardInterrupt):
        runtime.run(AgentSelection("com.example.sync-agent"), decision_context)

