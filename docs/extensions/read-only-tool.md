# Add a Read-only Tool

A Tool declares its namespace, JSON schemas, side effect, timeout, and required
capabilities in one `ToolSpec`.  Third-party names must not use the reserved
`core.*` namespace.

```python
from typing import Mapping

from benchmark.contracts import SideEffect, ToolContext, ToolResult, ToolSpec


class Quote:
    spec = ToolSpec(
        name="com.example.quotes.latest",
        description="Read the latest paper-market quote.",
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

    def invoke(self, context: ToolContext, arguments: Mapping[str, object]) -> ToolResult:
        symbol = str(arguments["symbol"]).upper()
        return ToolResult(ok=True, value={"symbol": symbol, "price": "100"})
```

Expose it from a provider:

```python
class Provider:
    def list_tools(self):
        return (Quote(),)
```

Then request the matching capability in the manifest:

```yaml
capabilities:
  requested:
    - market.read
```

`SideEffect.READ_ONLY` must not request `trading.write`, `memory.write`, or
`sandbox.write`.  The catalog checks this before an extension can load.  The
invoker validates arguments and results against the declared schemas, supplies
a cooperative deadline, and redacts credential-shaped fields in runtime
events.  The `ToolResult` returned to the Agent remains unredacted.

Use the reusable contract helper in the extension test suite:

```python
from benchmark.testing import ToolCase, assert_tool_contract

assert_tool_contract(
    Provider(),
    [ToolCase("com.example.quotes.latest", {"symbol": "BTC"}, expected_ok=True)],
)
```
