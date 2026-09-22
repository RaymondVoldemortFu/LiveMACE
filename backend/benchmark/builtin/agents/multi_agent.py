"""Built-in Multi-Agent factory and public SPI adapter."""

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
)
from benchmark.builtin.agents._legacy_ports import legacy_llm_surface, legacy_tool_registry
from benchmark.builtin.agents.react import _step_event_metadata
from benchmark.builtin.prompts import get_prompt_resolver
from benchmark.contracts import (
    AgentRunResult,
    AgentRuntimeError,
    DecisionContext,
    TerminationReason,
)
from services.agent.multi_agent import MultiAgent

MULTI_AGENT_COMPONENT_ID = "core.multi-agent"
MULTI_AGENT_VERSION = "1.0.0"
MULTI_AGENT_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "max_steps": {
            "type": "integer",
            "minimum": 1,
            "default": 15,
        },
        "user_id": {"type": ["string", "integer", "null"], "default": None},
        "agent_name": {"type": ["string", "null"], "default": None},
    },
}
MULTI_AGENT_SHIM_CONFIG_KEYS = frozenset(MULTI_AGENT_CONFIG_SCHEMA["properties"])
MULTI_AGENT_MAX_STEPS_REASON = (
    "MultiAgent Manager did not reach a conclusion within max steps."
)
MULTI_AGENT_DESCRIPTOR = AgentDescriptor(
    id=MULTI_AGENT_COMPONENT_ID,
    version=MULTI_AGENT_VERSION,
    config_schema=MULTI_AGENT_CONFIG_SCHEMA,
    display_name="Multi-Agent",
    description="Manager-coordinated trading, news, and coder specialists.",
)


def _unwrap_legacy_llm(llm: Any) -> Any:
    return legacy_llm_surface(
        llm,
        code="MULTI_AGENT_LEGACY_LLM_REQUIRED",
        message=(
            "core.multi-agent adapter requires a legacy LLMClient (call()) "
            "or an LLMClientPort (complete())"
        ),
    )


TOOL_FAILURE_CODES = frozenset({"SIZING_VALUE_INVALID", "IDEMPOTENCY_KEY_REQUIRED"})


def _raw_trade_items(decision: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    items = decision.get("executed_trades") or ()
    if not isinstance(items, (list, tuple)):
        return ()
    return tuple(item for item in items if isinstance(item, dict))


def _item_failure_codes(item: Mapping[str, Any]) -> tuple[str, ...]:
    codes: list[str] = []
    for key in ("reject_code", "error_code"):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            codes.append(raw.strip())
    return tuple(codes)


def _is_tool_failure_code(code: str) -> bool:
    return code.startswith("TOOL_") or code in TOOL_FAILURE_CODES


def _legacy_trades_are_tool_failures(decision: Mapping[str, Any]) -> bool:
    items = _raw_trade_items(decision)
    if not items:
        return False
    if any(item.get("executed") is True for item in items):
        return False
    return any(
        _is_tool_failure_code(code)
        for item in items
        for code in _item_failure_codes(item)
    )


def _termination_from_legacy_result(decision: Mapping[str, Any]) -> TerminationReason:
    if str(decision.get("reason") or "") == MULTI_AGENT_MAX_STEPS_REASON:
        return TerminationReason.MAX_STEPS
    if _legacy_trades_are_tool_failures(decision):
        return TerminationReason.TOOL_ERROR
    trades, incomplete, trade_errors = executed_trades_from_legacy(
        decision.get("executed_trades") or ()
    )
    if trades or incomplete or trade_errors:
        return TerminationReason.TRADE_DONE
    if str(decision.get("protocol") or "") == "tool" and decision.get("executed_trades"):
        return TerminationReason.TRADE_DONE
    return TerminationReason.HOLD


class MultiAgentAdapter:
    def __init__(self, agent: MultiAgent, *, events: EventSink) -> None:
        if not isinstance(agent, MultiAgent):
            raise TypeError("agent must be a MultiAgent")
        if not isinstance(events, EventSink):
            raise TypeError("events must implement EventSink")
        self._agent = agent
        self._events = events
        self.last_steps: tuple[dict[str, Any], ...] = ()

    @property
    def legacy_agent(self) -> MultiAgent:
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
                    agent_id=MULTI_AGENT_COMPONENT_ID,
                    agent_version=MULTI_AGENT_VERSION,
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
                "legacy MultiAgent must return a dict",
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
        if incomplete:
            metadata["incomplete_executed_trades"] = list(incomplete)
        if trade_errors:
            metadata["trade_errors"] = list(trade_errors)
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=_termination_from_legacy_result(legacy_result),
            executed_trades=trades,
            summary=str(legacy_result.get("reason") or ""),
            metadata=metadata,
        )


class MultiAgentFactory:
    def create(
        self,
        context: AgentBuildContext,
        config: Mapping[str, Any],
    ) -> MultiAgentAdapter:
        if not isinstance(context, AgentBuildContext):
            raise TypeError("context must be AgentBuildContext")
        agent = MultiAgent(
            _unwrap_legacy_llm(context.llm),
            legacy_tool_registry(context.tools),
            max_steps=int(config["max_steps"]),
            user_id=config.get("user_id"),
            agent_name=config.get("agent_name"),
            prompt_resolver=get_prompt_resolver(context.prompts),
        )
        return MultiAgentAdapter(agent, events=context.events)


__all__ = [
    "MULTI_AGENT_COMPONENT_ID",
    "MULTI_AGENT_CONFIG_SCHEMA",
    "MULTI_AGENT_DESCRIPTOR",
    "MULTI_AGENT_SHIM_CONFIG_KEYS",
    "MULTI_AGENT_VERSION",
    "MultiAgentAdapter",
    "MultiAgentFactory",
]
