from benchmark.contracts import MARKET_READ, SideEffect, ToolResult, ToolSpec


class SymbolEchoTool:
    def __init__(self):
        self._spec = ToolSpec(
            name="com.example.symbol-echo",
            description="Echo a symbol for read-only inspection.",
            input_schema={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
            output_schema={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
            },
            side_effect=SideEffect.READ_ONLY,
            required_capabilities=(MARKET_READ,),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, context, arguments) -> ToolResult:
        del context
        return ToolResult(ok=True, value={"symbol": arguments["symbol"]})


class ReadOnlyToolProvider:
    def list_tools(self):
        return (SymbolEchoTool(),)
