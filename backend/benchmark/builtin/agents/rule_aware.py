"""Built-in Rule-Aware Agent factory and public synchronous SPI adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from benchmark.agents import (
    AgentBuildContext,
    AgentDescriptor,
    AgentRuntimeEvent,
    EventSink,
)
from benchmark.builtin.agents._legacy_context import (
    nested_executed_trades_from_legacy,
    portfolio_from_context,
    prices_from_context,
    termination_from_legacy_decision,
)
from benchmark.builtin.agents._legacy_ports import tool_error_payload
from benchmark.builtin.prompts import get_prompt_resolver
from benchmark.contracts import (
    AgentRunResult,
    AgentRuntimeError,
    DecisionContext,
    JsonValue,
    ToolResult,
    ToolRuntimeError,
    to_jsonable,
)
from benchmark.providers import LLMClientPort, LLMRequest, LLMResponse, LLMToolCall
from benchmark.tools import ToolInvoker, ToolSpecSource, openai_tool_schema
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
        "max_steps": {
            "type": "integer",
            "minimum": 1,
            "default": AgentConfig.MAX_STEPS,
        },
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


_LEGACY_TO_PUBLIC_TOOL_NAMES = {
    "execute_trade": "core.execute_trade",
    "get_market_snapshot": "core.market_snapshot",
    "get_kline_history": "core.kline_history",
    "get_account_state": "core.account_state",
    "get_history_decisions": "core.decision_history",
    "memory_add": "core.memory_add",
    "memory_search": "core.memory_search",
    "consult_search_agent": "core.search",
    "execute_shell_command": "core.execute_shell_command",
    "read_file": "core.read_file",
    "write_file": "core.write_file",
    "run_python_script": "core.run_python_script",
}
_PUBLIC_TO_LEGACY_TOOL_NAMES = {
    public: legacy for legacy, public in _LEGACY_TO_PUBLIC_TOOL_NAMES.items()
}
_LEGACY_TRADE_RUNTIME_ARGUMENTS = frozenset(
    {"decision_round_id", "tool_call_id"}
)


@dataclass(frozen=True)
class _LegacyLLMMessage:
    content: str
    tool_calls: list[dict[str, JsonValue]]
    assistant_message: dict[str, JsonValue]


class _PublicLLMBridge:
    """Expose the legacy loop surface while invoking only LLMClientPort.complete()."""

    def __init__(self, llm: LLMClientPort) -> None:
        self._llm = llm

    def call(
        self,
        messages: Sequence[Mapping[str, JsonValue]],
        tools: Sequence[Mapping[str, JsonValue]] | None = None,
        **_: Any,
    ) -> _LegacyLLMMessage:
        response = self._llm.complete(
            LLMRequest(
                messages=tuple(dict(message) for message in messages),
                model=None,
                tools=tuple(dict(tool) for tool in (tools or ())),
            )
        )
        if not isinstance(response, LLMResponse):
            raise AgentRuntimeError(
                "LLMClientPort.complete() must return LLMResponse",
                code="RULE_AWARE_LLM_RESULT_INVALID",
            )
        tool_calls = _legacy_tool_calls(response)
        assistant_message = _assistant_message(response, tool_calls)
        return _LegacyLLMMessage(response.content, tool_calls, assistant_message)

    @staticmethod
    def build_assistant_message_dict(
        response: _LegacyLLMMessage,
    ) -> dict[str, JsonValue]:
        return dict(response.assistant_message)

    def requires_post_tool_user_message(self) -> bool:
        capability = getattr(self._llm, "requires_post_tool_user_message", None)
        return bool(capability()) if callable(capability) else False


class _PublicToolBridge:
    """Expose the legacy registry surface while invoking only ToolInvoker.call()."""

    def __init__(
        self,
        tools: ToolInvoker,
        model_tools: Sequence[Mapping[str, JsonValue]],
    ) -> None:
        self._tools = tools
        self.openai_tools = [dict(tool) for tool in model_tools]

    def get(self, name: str):
        public_name = _public_tool_name(name)

        def invoke(**arguments: Any) -> JsonValue:
            public_arguments = dict(arguments)
            if (
                not isinstance(self._tools, ToolRegistry)
                and public_name == "core.execute_trade"
            ):
                public_arguments = {
                    key: value
                    for key, value in arguments.items()
                    if key not in _LEGACY_TRADE_RUNTIME_ARGUMENTS
                }
            try:
                call_id = arguments.get("tool_call_id") if public_name == "core.execute_trade" else None
                call_with_id = getattr(self._tools, "call_with_id", None)
                if call_id and callable(call_with_id):
                    result = call_with_id(public_name, public_arguments, call_id)
                else:
                    result = self._tools.call(public_name, public_arguments)
            except ToolRuntimeError as exc:
                if isinstance(self._tools, ToolRegistry) and isinstance(
                    exc.__cause__, Exception
                ):
                    raise exc.__cause__
                raise
            if not isinstance(result, ToolResult):
                raise AgentRuntimeError(
                    "ToolInvoker.call() must return ToolResult",
                    code="RULE_AWARE_TOOL_RESULT_INVALID",
                    details={"tool_name": public_name},
                )
            if result.ok:
                return to_jsonable(result.value)
            payload = tool_error_payload(result)
            if public_name == "core.execute_trade":
                payload["executed"] = False
                payload["reject_code"] = result.error_code
            return payload

        return invoke


def _legacy_tool_calls(response: LLMResponse) -> list[dict[str, JsonValue]]:
    raw_calls = response.raw.get("tool_calls")
    raw_sequence = raw_calls if isinstance(raw_calls, (list, tuple)) else ()
    return [
        _legacy_tool_call(
            tool_call, raw_sequence[index] if index < len(raw_sequence) else None
        )
        for index, tool_call in enumerate(response.tool_calls)
    ]


def _legacy_tool_call(
    tool_call: LLMToolCall,
    raw: JsonValue,
) -> dict[str, JsonValue]:
    payload = dict(raw) if isinstance(raw, Mapping) else {}
    raw_function = payload.get("function")
    function = dict(raw_function) if isinstance(raw_function, Mapping) else {}
    function["name"] = _legacy_tool_name(tool_call.name)
    function["arguments"] = json.dumps(
        to_jsonable(tool_call.arguments),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    payload["id"] = tool_call.id
    payload["type"] = str(payload.get("type") or "function")
    payload["function"] = function
    return to_jsonable(payload)


def _assistant_message(
    response: LLMResponse,
    tool_calls: list[dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    message = dict(response.raw)
    message["role"] = "assistant"
    message["content"] = response.content
    if tool_calls:
        message["tool_calls"] = tool_calls
    else:
        message.pop("tool_calls", None)
    return to_jsonable(message)


def _public_tool_name(name: str) -> str:
    normalized = name.rsplit(":", 1)[-1]
    return _LEGACY_TO_PUBLIC_TOOL_NAMES.get(normalized, normalized)


def _legacy_tool_name(name: str) -> str:
    return _PUBLIC_TO_LEGACY_TOOL_NAMES.get(name, name)


def _model_tools(tools: ToolInvoker) -> tuple[dict[str, JsonValue], ...]:
    if isinstance(tools, ToolSpecSource):
        schemas = (openai_tool_schema(spec) for spec in tools.list_specs())
    elif isinstance(tools, ToolRegistry):
        schemas = iter(tools.openai_tools)
    else:
        return ()
    normalized: list[dict[str, JsonValue]] = []
    for schema in schemas:
        payload = to_jsonable(schema)
        function = payload.get("function")
        if not isinstance(function, dict):
            raise AgentRuntimeError(
                "Tool schema must contain a function object",
                code="RULE_AWARE_TOOL_SCHEMA_INVALID",
            )
        function["name"] = _legacy_tool_name(str(function.get("name") or ""))
        normalized.append(payload)
    return tuple(normalized)


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

        try:
            legacy_result = self._agent.run(
                portfolio=portfolio_from_context(context),
                prices=prices_from_context(context),
                on_step=on_step,
                trace_id=context.trace_id,
                decision_round_id=context.decision_round_id,
            )
        finally:
            auditor = getattr(self._agent, "llm_auditor", None)
            if auditor is not None:
                auditor.llm_client.close()
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


def _step_event_metadata(
    step_number: int, message: Mapping[str, Any]
) -> dict[str, Any]:
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
        rule_docs_path = config.get("rule_docs_path") or _default_rule_documents_path()
        rule_engine = RuleEngine(str(rule_docs_path))
        agent = RuleAwareAgent(
            _PublicLLMBridge(context.llm),
            _PublicToolBridge(context.tools, _model_tools(context.tools)),
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
