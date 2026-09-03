"""A capability-scoped, read-only Tool example."""

from __future__ import annotations

from typing import Mapping

from benchmark.contracts import Market, SideEffect, ToolContext, ToolResult, ToolSpec


class QuoteTool:
    spec = ToolSpec(
        name="examples.read-only-tool.quote",
        description="Return a deterministic paper-market quote.",
        input_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "minLength": 1},
                "market": {"type": "string", "enum": ["CRYPTO", "US"]},
            },
            "required": ["symbol", "market"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "market": {"type": "string"},
                "price": {"type": "string"},
            },
            "required": ["symbol", "market", "price"],
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
        market = Market(str(arguments["market"]).upper())
        symbol = str(arguments["symbol"]).strip().upper()
        # Strings keep the DTO JSON boundary explicit and deterministic.
        price = "100" if market is Market.CRYPTO else "200"
        return ToolResult(
            ok=True,
            value={"symbol": symbol, "market": market.value, "price": price},
        )


class ReadOnlyToolProvider:
    id = "examples.read-only-tool"
    version = "1.0.0"

    def list_tools(self) -> tuple[QuoteTool, ...]:
        return (QuoteTool(),)
