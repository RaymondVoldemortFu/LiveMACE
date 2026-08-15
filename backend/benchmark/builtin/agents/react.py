"""Built-in ReAct Agent factory and public SPI adapter.

``react`` and ``react_tool`` share component id ``core.react``. The difference is
``config.tool_routing_enabled``, matching the current account flag.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

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
from benchmark.builtin.prompts import get_prompt_resolver
from benchmark.contracts import (
    AgentRunResult,
    AgentRuntimeError,
    ComponentConfigError,
    DecisionContext,
    to_jsonable,
)
from config.agent_config import AgentConfig
from services.agent.react import ReActAgent
from services.agent.tools import ToolRegistry


REACT_COMPONENT_ID = "core.react"
REACT_VERSION = "1.0.0"

REACT_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "max_steps": {
            "type": "integer",
            "minimum": 1,
            "default": AgentConfig.MAX_STEPS,
        },
        "tool_routing_enabled": {
            "type": "boolean",
            "default": bool(AgentConfig.AGENT_ENABLE_TOOL_ROUTING),
        },
        "memory_enabled": {"type": ["boolean", "null"], "default": None},
        "step_reminder_threshold": {
            "type": "integer",
            "minimum": 0,
            "default": AgentConfig.STEP_REMINDER_THRESHOLD,
        },
        "include_simulation_notice": {
            "type": "boolean",
            "default": bool(AgentConfig.AGENT_INCLUDE_SIMULATION_NOTICE),
        },
        "agent_name": {"type": ["string", "null"], "default": None},
        "user_id": {"type": ["string", "integer", "null"], "default": None},
    },
}

REACT_DESCRIPTOR = AgentDescriptor(
    id=REACT_COMPONENT_ID,
    version=REACT_VERSION,
    config_schema=REACT_CONFIG_SCHEMA,
    display_name="ReAct",
    description=(
        "Built-in ReAct trading Agent. Set tool_routing_enabled to select "
        "the react_tool (dynamic select_tools) or react_plain path."
    ),
)

REACT_SHIM_CONFIG_KEYS = frozenset(REACT_CONFIG_SCHEMA["properties"])
MEMORY_TOOL_NAMES = ("memory_add", "memory_search")


def memory_tools_registered(tools: ToolRegistry) -> bool:
    return any(name in tools.tools for name in MEMORY_TOOL_NAMES)


def resolve_memory_enabled(config: Mapping[str, Any], tools: ToolRegistry) -> bool:
    raw = config.get("memory_enabled")
    if raw is None:
        return memory_tools_registered(tools)
    return bool(raw)


def _unwrap_legacy_llm(llm: Any) -> Any:
    legacy = getattr(llm, "legacy_client", None)
    if legacy is not None and callable(getattr(legacy, "call", None)):
        return legacy
    if callable(getattr(llm, "call", None)):
        return llm
    raise ComponentConfigError(
        "core.react adapter requires a legacy LLMClient (call()) "
        "or LegacyLLMClientAdapter",
        code="REACT_LEGACY_LLM_REQUIRED",
    )


class ReActAgentAdapter:
    """Adapt the existing ReActAgent to the public synchronous Agent SPI."""

    def __init__(
        self,
        agent: ReActAgent,
        *,
        events: EventSink,
        agent_id: str = REACT_COMPONENT_ID,
        agent_version: str = REACT_VERSION,
    ) -> None:
        if not isinstance(agent, ReActAgent):
            raise TypeError("agent must be ReActAgent")
        if not isinstance(events, EventSink):
            raise TypeError("events must implement EventSink")
        self._agent = agent
        self._events = events
        self._agent_id = agent_id
        self._agent_version = agent_version
        self.last_steps: tuple[dict[str, Any], ...] = ()

    @property
    def legacy_agent(self) -> ReActAgent:
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
                    agent_id=self._agent_id,
                    agent_version=self._agent_version,
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
                "legacy ReActAgent must return a dict",
                code="INVALID_LEGACY_RESULT",
            )
        self.last_steps = tuple(steps)
        termination = termination_from_legacy_decision(legacy_result)
        trades, incomplete, trade_errors = executed_trades_from_legacy(
            legacy_result.get("executed_trades") or ()
        )
        metadata: dict[str, Any] = {
            "legacy_protocol": str(legacy_result.get("protocol") or "unknown"),
            "step_count": len(steps),
        }
        if incomplete:
            metadata["incomplete_executed_trades"] = list(incomplete)
        if trade_errors:
            metadata["trade_errors"] = list(trade_errors)
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=termination,
            executed_trades=trades,
            summary=str(legacy_result.get("reason") or ""),
            metadata=metadata,
        )


def _step_event_metadata(step_number: int, message: Mapping[str, Any]) -> dict[str, Any]:
    role = message.get("role")
    if not isinstance(role, str) or not role:
        raise AgentRuntimeError(
            "on_step message must include a non-empty role",
            code="AGENT_STEP_EVENT_INVALID",
            details={"step_number": step_number, "field": "role"},
        )
    payload: dict[str, Any] = {
        "step_number": step_number,
        "role": role,
    }
    for key in ("content", "name", "tool_call_id", "tool_calls"):
        if key not in message:
            continue
        try:
            payload[key] = to_jsonable(message[key])
        except (TypeError, ValueError) as exc:
            raise AgentRuntimeError(
                "on_step message field is not JSON-serializable",
                code="AGENT_STEP_EVENT_INVALID",
                details={"field": key, "step_number": step_number},
            ) from exc
    return payload


class ReActAgentFactory:
    """Create a ReAct adapter from public AgentBuildContext ports."""

    def create(
        self,
        context: AgentBuildContext,
        config: Mapping[str, Any],
    ) -> ReActAgentAdapter:
        if not isinstance(context, AgentBuildContext):
            raise TypeError("context must be AgentBuildContext")
        if not isinstance(context.tools, ToolRegistry):
            raise ComponentConfigError(
                "core.react currently requires services.agent.tools.ToolRegistry; "
                "public ToolInvoker-only construction is owned by M06",
                code="REACT_LEGACY_TOOL_REGISTRY_REQUIRED",
            )
        llm = _unwrap_legacy_llm(context.llm)
        agent = ReActAgent(
            llm,
            context.tools,
            max_steps=int(config["max_steps"]),
            user_id=config.get("user_id"),
            agent_name=config.get("agent_name"),
            tool_routing_enabled=bool(config["tool_routing_enabled"]),
            memory_enabled=resolve_memory_enabled(config, context.tools),
            step_reminder_threshold=int(config["step_reminder_threshold"]),
            include_simulation_notice=bool(config["include_simulation_notice"]),
            prompt_resolver=get_prompt_resolver(context.prompts),
        )
        return ReActAgentAdapter(
            agent,
            events=context.events,
            agent_id=REACT_COMPONENT_ID,
            agent_version=REACT_VERSION,
        )


__all__ = [
    "REACT_COMPONENT_ID",
    "REACT_CONFIG_SCHEMA",
    "REACT_DESCRIPTOR",
    "REACT_SHIM_CONFIG_KEYS",
    "REACT_VERSION",
    "MEMORY_TOOL_NAMES",
    "memory_tools_registered",
    "resolve_memory_enabled",
    "ReActAgentAdapter",
    "ReActAgentFactory",
]
