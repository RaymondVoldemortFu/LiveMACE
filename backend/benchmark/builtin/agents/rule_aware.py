"""Built-in Rule-Aware Agent factory and public synchronous SPI adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from benchmark.agents import AgentBuildContext, AgentDescriptor, AgentRuntimeEvent, EventSink
from benchmark.builtin.agents._legacy_context import (
    nested_executed_trades_from_legacy,
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
from services.agent.rule_aware.rule_aware_agent import RuleAwareAgent
from services.agent.rule_aware.rule_engine import RuleEngine
from services.agent.tools import ToolRegistry


RULE_AWARE_COMPONENT_ID = "core.rule-aware"
RULE_AWARE_VERSION = "1.0.0"
RULE_AWARE_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "max_steps": {"type": "integer", "minimum": 1, "default": AgentConfig.MAX_STEPS},
        "user_id": {"type": ["string", "integer", "null"], "default": None},
        "account_id": {"type": ["integer", "null"], "minimum": 1, "default": None},
        "agent_name": {"type": ["string", "null"], "default": None},
        "rule_docs_path": {"type": ["string", "null"], "default": None},
        "enable_llm_audit": {"type": "boolean", "default": False},
    },
}

RULE_AWARE_DESCRIPTOR = AgentDescriptor(
    id=RULE_AWARE_COMPONENT_ID,
    version=RULE_AWARE_VERSION,
    config_schema=RULE_AWARE_CONFIG_SCHEMA,
    display_name="Rule-Aware",
    description=(
        "Built-in trading Agent that applies the configured rule documents "
        "and attaches compliance audit data."
    ),
)

RULE_AWARE_SHIM_CONFIG_KEYS = frozenset(RULE_AWARE_CONFIG_SCHEMA["properties"])


def _default_rule_documents_path() -> str:
    return str(Path(__file__).resolve().parents[3] / "config" / "rules")


def _unwrap_legacy_llm(llm: Any) -> Any:
    legacy = getattr(llm, "legacy_client", None)
    if legacy is not None and callable(getattr(legacy, "call", None)):
        return legacy
    if callable(getattr(llm, "call", None)):
        return llm
    raise ComponentConfigError(
        "core.rule-aware adapter requires a legacy LLMClient (call()) "
        "or LegacyLLMClientAdapter",
        code="RULE_AWARE_LEGACY_LLM_REQUIRED",
    )


class RuleAwareAgentAdapter:
    """Adapt the existing synchronous RuleAwareAgent to the public Agent SPI."""

    def __init__(
        self,
        agent: RuleAwareAgent,
        *,
        events: EventSink,
        agent_id: str = RULE_AWARE_COMPONENT_ID,
        agent_version: str = RULE_AWARE_VERSION,
    ) -> None:
        if not isinstance(agent, RuleAwareAgent):
            raise TypeError("agent must be RuleAwareAgent")
        if not isinstance(events, EventSink):
            raise TypeError("events must implement EventSink")
        self._agent = agent
        self._events = events
        self._agent_id = agent_id
        self._agent_version = agent_version
        self.last_steps: tuple[dict[str, Any], ...] = ()

    @property
    def legacy_agent(self) -> RuleAwareAgent:
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
                "legacy RuleAwareAgent must return a dict",
                code="INVALID_LEGACY_RESULT",
            )
        self.last_steps = tuple(steps)
        termination = termination_from_legacy_decision(legacy_result)
        trades, incomplete, trade_errors = nested_executed_trades_from_legacy(
            legacy_result.get("executed_trades") or ()
        )
        metadata: dict[str, Any] = {
            "legacy_protocol": str(legacy_result.get("protocol") or "unknown"),
            "step_count": len(steps),
        }
        for key in ("compliance_audit", "llm_audit", "agent_reasoning"):
            if key not in legacy_result:
                continue
            metadata[key] = _jsonable_field(legacy_result[key], key)
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


def _jsonable_field(value: Any, field_name: str) -> Any:
    try:
        return to_jsonable(value)
    except (TypeError, ValueError) as exc:
        raise AgentRuntimeError(
            f"legacy RuleAwareAgent field {field_name!r} is not JSON-serializable",
            code="INVALID_LEGACY_RESULT",
            details={"field": field_name},
        ) from exc


def _step_event_metadata(step_number: int, message: Mapping[str, Any]) -> dict[str, Any]:
    role = message.get("role")
    if not isinstance(role, str) or not role:
        raise AgentRuntimeError(
            "on_step message must include a non-empty role",
            code="AGENT_STEP_EVENT_INVALID",
            details={"step_number": step_number, "field": "role"},
        )
    payload: dict[str, Any] = {"step_number": step_number, "role": role}
    for key in ("content", "name", "tool_call_id", "tool_calls"):
        value = message.get(key)
        if key == "tool_call_id" and value is None:
            nested = message.get("metadata")
            if isinstance(nested, Mapping):
                value = nested.get("tool_call_id")
        if key not in message and value is None:
            continue
        payload[key] = _jsonable_field(value, key)
    return payload


class RuleAwareAgentFactory:
    """Create a Rule-Aware adapter from public AgentBuildContext ports."""

    def create(
        self,
        context: AgentBuildContext,
        config: Mapping[str, Any],
    ) -> RuleAwareAgentAdapter:
        if not isinstance(context, AgentBuildContext):
            raise TypeError("context must be AgentBuildContext")
        if not isinstance(context.tools, ToolRegistry):
            raise ComponentConfigError(
                "core.rule-aware currently requires services.agent.tools.ToolRegistry; "
                "public ToolInvoker-only construction is owned by M06",
                code="RULE_AWARE_LEGACY_TOOL_REGISTRY_REQUIRED",
            )
        llm = _unwrap_legacy_llm(context.llm)
        rule_docs_path = config.get("rule_docs_path") or _default_rule_documents_path()
        rule_engine = RuleEngine(str(rule_docs_path))
        agent = RuleAwareAgent(
            llm,
            context.tools,
            rule_engine,
            max_steps=int(config["max_steps"]),
            user_id=config.get("user_id"),
            account_id=config.get("account_id"),
            enable_llm_audit=bool(config["enable_llm_audit"]),
            agent_name=config.get("agent_name"),
            prompt_resolver=get_prompt_resolver(context.prompts),
        )
        return RuleAwareAgentAdapter(
            agent,
            events=context.events,
            agent_id=RULE_AWARE_COMPONENT_ID,
            agent_version=RULE_AWARE_VERSION,
        )


__all__ = [
    "RULE_AWARE_COMPONENT_ID",
    "RULE_AWARE_CONFIG_SCHEMA",
    "RULE_AWARE_DESCRIPTOR",
    "RULE_AWARE_SHIM_CONFIG_KEYS",
    "RULE_AWARE_VERSION",
    "RuleAwareAgentAdapter",
    "RuleAwareAgentFactory",
]
