# Extension SDK

These examples are complete extension directories. They load through the same Catalog path as built-in components.

## Ten-minute Agent

1. Copy `examples/extensions/minimal-agent`.
2. Implement a synchronous `run(context)` that returns `AgentRunResult`.
3. Validate and test:

```bash
cd backend
uv run alpha-arena extension validate ../examples/extensions/minimal-agent
uv run alpha-arena extension test ../examples/extensions/minimal-agent
uv run alpha-arena extension list ../examples/extensions/minimal-agent
```

`Agent.run()` is synchronous. You may use asyncio or threads inside `run()`, but the runtime does not schedule, await, or supervise that work.

## Read-only Tool

Copy `examples/extensions/read-only-tool`. A Tool lists a `ToolSpec` and implements `invoke(context, arguments) -> ToolResult`. Request only the capabilities you need. `trading.write` is reserved for `core.execute_trade`.

## Prompt-only override

Copy `examples/extensions/prompt-override`. A Prompt-only extension is YAML plus text files. No Python module is required.

## Combined extension

`examples/extensions/combined-extension` contributes an Agent, a Tool, and a Prompt profile from one manifest.

## Public SPI

Import only:

- `benchmark.contracts`
- `benchmark.agents`
- `benchmark.tools`
- `benchmark.prompts`
- `benchmark.providers`
- `benchmark.extensions`
- `benchmark.testing`

Contract helpers and fakes:

```python
from benchmark.testing import (
    AgentCase,
    assert_agent_contract,
    assert_tool_contract,
    assert_prompt_contract,
    build_fake_context,
    FakeLLMClientPort,
    FakeMarketDataPort,
    FakeMemoryStorePort,
    FakeSandboxPort,
    FakeTradeCommandGateway,
)
```

`FakeTradeCommandGateway.execute()` records commands in memory and does not open a database session.

Public API version is `1`. Compatible releases stay on SemVer within that API version; a new `api_version` is required for breaking SPI changes.
