# Public SPI and Version Rules

For complete DTOs, method signatures, configuration, and error contracts, see the [v1 interface reference](interface-reference.md).

Extension authors may import from these stable namespaces:

| Namespace | Main types |
| --- | --- |
| `benchmark.contracts` | `DecisionContext`, DTOs, `ToolSpec`, `ToolResult`, `AgentRunResult` |
| `benchmark.agents` | `Agent`, `AgentFactory`, `AgentBuildContext` |
| `benchmark.tools` | `Tool`, `ToolProvider`, `ToolInvoker`, `ToolSpecSource` |
| `benchmark.prompts` | `PromptProvider`, `PromptResolver`, Prompt DTOs |
| `benchmark.providers` | synchronous LLM, market, memory, and sandbox ports |
| `benchmark.extensions` | manifest, discovery, validation, and catalog APIs |
| `benchmark.testing` | fake ports, fixtures, and contract assertions |

The v1 SPI is synchronous.  `Agent.run`, `AgentFactory.create`, Tool methods,
Prompt methods, and Provider methods must return their DTO before returning to
the caller.  An awaitable is a contract failure.  An extension may use
`asyncio` or a thread pool internally, provided its public method remains
synchronous and owns all of those resources itself.

## SemVer and API versions

- `api_version` identifies the framework contract.  A v1 loader accepts only
  `api_version: 1`.
- `version` is the extension's SemVer release and is recorded with every
  component.
- Agent and Prompt selections can pin a component version.  Omitting a pin
  selects the highest available compatible version.
- A breaking SPI change requires a new API version.  Additive fields and
  defaults are the compatible way to evolve v1.

An upgrade keeps the previous release loadable by publishing a second package
with the same extension `id` and a new SemVer `version`.  Catalog loading
retains both `1.0.0` and `2.0.0` when they share `api_version: 1`:

```yaml
id: com.example.quotes
version: 1.0.0
api_version: 1
```

```yaml
id: com.example.quotes
version: 2.0.0
api_version: 1
```

An account pins the component it should run:

```json
{
  "agent_id": "com.example.quotes.agent",
  "component_versions": {
    "com.example.quotes.agent": "2.0.0"
  }
}
```

Another account can keep `1.0.0` in `component_versions` while both releases
remain installed.

## Capabilities

Manifest capabilities are an upper bound granted by the host.  A Tool must
declare the capabilities it needs in `ToolSpec.required_capabilities`, and its
side effect must agree with that declaration:

| Side effect | Typical capability |
| --- | --- |
| `read_only` | none or a read capability |
| `external_read` | `market.read` or `network.read` |
| `memory_write` | `memory.write` |
| `sandbox_write` | `sandbox.write` |
| `trading_write` | `trading.write` |

Read-only examples deliberately request no write capability.  In particular,
`read-only-tool` cannot obtain `trading.write` merely by calling the Tool.
