"""Combined public Agent and read-only Tool example."""

from __future__ import annotations

from typing import Mapping

from benchmark.agents import AgentBuildContext
from benchmark.contracts import (
    AgentRunResult,
    DecisionContext,
    SideEffect,
    TerminationReason,
    ToolContext,
    ToolResult,
    ToolSpec,
)


class CombinedQuoteTool:
    spec = ToolSpec(
        name="examples.combined-extension.quote",
        description="Read a deterministic quote for the combined example.",
        input_schema={
            "type": "object",
            "properties": {"symbol": {"type": "string", "minLength": 1}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "price": {"type": "string"}},
            "required": ["symbol", "price"],
            "additionalProperties": False,
        },
        side_effect=SideEffect.READ_ONLY,
        timeout_seconds=5.0,
        required_capabilities=("market.read",),
    )

    def invoke(
        self,
        context: ToolContext,
        arguments: Mapping[str, object],
    ) -> ToolResult:
        del context
        symbol = str(arguments["symbol"]).strip().upper()
        return ToolResult(ok=True, value={"symbol": symbol, "price": "100"})


class CombinedToolProvider:
    id = "examples.combined-extension"
    version = "1.0.0"

    def list_tools(self) -> tuple[CombinedQuoteTool, ...]:
        return (CombinedQuoteTool(),)


class CombinedAgent:
    def __init__(self, build_context: AgentBuildContext, symbol: str) -> None:
        self._build_context = build_context
        self._symbol = symbol

    def run(self, context: DecisionContext) -> AgentRunResult:
        quote = self._build_context.tools.call(
            "examples.combined-extension.quote",
            {"symbol": self._symbol},
        )
        if quote.ok:
            summary = f"Observed {quote.value['symbol']} at {quote.value['price']}; holding."
        else:
            summary = "Quote was unavailable; holding without a trade."
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=TerminationReason.HOLD,
            summary=summary,
        )


class CombinedAgentFactory:
    def create(
        self,
        context: AgentBuildContext,
        config: Mapping[str, object],
    ) -> CombinedAgent:
        symbol = config.get("symbol", "BTC")
        if not isinstance(symbol, str) or not symbol.strip():
            raise TypeError("symbol must be a non-empty string")
        return CombinedAgent(context, symbol)
