"""Built-in Advanced Multi-Agent factory and public SPI adapter."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from benchmark.agents import (
    AgentBuildContext,
    AgentDescriptor,
    AgentRuntimeEvent,
    EventSink,
)
from benchmark.builtin.agents._legacy_context import (
    executed_trades_from_legacy,
    portfolio_from_context,
    prices_from_context,
    termination_from_legacy_decision,
)
from benchmark.builtin.agents._legacy_ports import legacy_llm_surface, legacy_tool_registry
from benchmark.builtin.agents.react import _step_event_metadata
from benchmark.builtin.prompts import get_prompt_resolver
from benchmark.contracts import (
    AgentRunResult,
    AgentRuntimeError,
    DecisionContext,
)
from services.agent.multi_agent_advanced import AdvancedMultiAgent

ADVANCED_MULTI_AGENT_COMPONENT_ID = "core.advanced-multi-agent"
ADVANCED_MULTI_AGENT_VERSION = "1.0.0"
ADVANCED_MULTI_AGENT_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "max_steps": {
            "type": "integer",
            "minimum": 1,
            "default": 30,
        },
        "user_id": {"type": ["string", "integer", "null"], "default": None},
        "agent_name": {"type": ["string", "null"], "default": None},
    },
}
ADVANCED_MULTI_AGENT_SHIM_CONFIG_KEYS = frozenset(
    ADVANCED_MULTI_AGENT_CONFIG_SCHEMA["properties"]
)
ADVANCED_MULTI_AGENT_DESCRIPTOR = AgentDescriptor(
    id=ADVANCED_MULTI_AGENT_COMPONENT_ID,
    version=ADVANCED_MULTI_AGENT_VERSION,
    config_schema=ADVANCED_MULTI_AGENT_CONFIG_SCHEMA,
    display_name="Advanced Multi-Agent",
    description=(
        "Manager-coordinated specialist analysis with explicit trade execution."
    ),
)


def _unwrap_legacy_llm(llm: Any) -> Any:
    return legacy_llm_surface(
        llm,
        code="ADVANCED_MULTI_AGENT_LEGACY_LLM_REQUIRED",
        message=(
            "core.advanced-multi-agent adapter requires a legacy LLMClient (call()) "
            "or an LLMClientPort (complete())"
        ),
    )


class AdvancedMultiAgentAdapter:
    def __init__(self, agent: AdvancedMultiAgent, *, events: EventSink) -> None:
        if not isinstance(agent, AdvancedMultiAgent):
            raise TypeError("agent must be an AdvancedMultiAgent")
        if not isinstance(events, EventSink):
            raise TypeError("events must implement EventSink")
        self._agent = agent
        self._events = events
        self.last_steps: tuple[dict[str, Any], ...] = ()

    @property
    def legacy_agent(self) -> AdvancedMultiAgent:
        return self._agent

    def run(self, context: DecisionContext) -> AgentRunResult:
        if not isinstance(context, DecisionContext):
            raise TypeError("context must be DecisionContext")
        steps: list[dict[str, Any]] = []

        def on_step(message: dict[str, Any]) -> None:
            if not isinstance(message, dict):
                raise TypeError("on_step message must be a dict")
            recorded = dict(message)
            steps.append(recorded)
            self._events.emit(
                AgentRuntimeEvent(
                    type="agent.step",
                    agent_id=ADVANCED_MULTI_AGENT_COMPONENT_ID,
                    agent_version=ADVANCED_MULTI_AGENT_VERSION,
                    trace_id=context.trace_id,
                    decision_round_id=context.decision_round_id,
                    occurred_at=datetime.now(timezone.utc),
                    metadata=_step_event_metadata(len(steps), recorded),
                )
            )

        legacy_result = self._agent.run(
            portfolio=portfolio_from_context(context),
            prices=prices_from_context(context),
            on_step=on_step,
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
        )
        if not isinstance(legacy_result, dict):
            raise AgentRuntimeError(
                "legacy AdvancedMultiAgent must return a dict",
                code="INVALID_LEGACY_RESULT",
            )
        self.last_steps = tuple(steps)
        trades, incomplete, trade_errors = executed_trades_from_legacy(
            legacy_result.get("executed_trades") or ()
        )
        metadata: dict[str, Any] = {
            "legacy_protocol": str(legacy_result.get("protocol") or "decision"),
            "legacy_operation": str(legacy_result.get("operation") or ""),
            "step_count": len(steps),
        }
        for key in (
            "execution_complete",
            "expected_execution_calls",
            "completed_execution_calls",
        ):
            if key in legacy_result:
                metadata[key] = legacy_result[key]
        if incomplete:
            metadata["incomplete_executed_trades"] = list(incomplete)
        if trade_errors:
            metadata["trade_errors"] = list(trade_errors)
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=termination_from_legacy_decision(legacy_result),
            executed_trades=trades,
            summary=str(legacy_result.get("reason") or ""),
            metadata=metadata,
        )


class AdvancedMultiAgentFactory:
    def create(
        self,
        context: AgentBuildContext,
        config: Mapping[str, Any],
    ) -> AdvancedMultiAgentAdapter:
        if not isinstance(context, AgentBuildContext):
            raise TypeError("context must be AgentBuildContext")
        agent = AdvancedMultiAgent(
            _unwrap_legacy_llm(context.llm),
            legacy_tool_registry(context.tools),
            max_steps=int(config["max_steps"]),
            user_id=config.get("user_id"),
            agent_name=config.get("agent_name"),
            prompt_resolver=get_prompt_resolver(context.prompts),
        )
        return AdvancedMultiAgentAdapter(agent, events=context.events)


__all__ = [
    "ADVANCED_MULTI_AGENT_COMPONENT_ID",
    "ADVANCED_MULTI_AGENT_CONFIG_SCHEMA",
    "ADVANCED_MULTI_AGENT_DESCRIPTOR",
    "ADVANCED_MULTI_AGENT_SHIM_CONFIG_KEYS",
    "ADVANCED_MULTI_AGENT_VERSION",
    "AdvancedMultiAgentAdapter",
    "AdvancedMultiAgentFactory",
]
