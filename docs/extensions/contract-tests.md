# Contract Tests and Fake Ports

The `benchmark.testing` package provides deterministic fixtures that do not
open a database or contact an external service:

```python
from benchmark.testing import (
    AgentCase,
    ToolCase,
    assert_agent_contract,
    assert_prompt_contract,
    assert_tool_contract,
    build_fake_context,
    FakeEventSink,
    FakeLLMClientPort,
    FakeMarketDataPort,
    FakeMemoryStorePort,
    FakeSandboxPort,
    FakeTradeGateway,
)
```

`build_fake_context()` returns an immutable `DecisionContext` with a stable
account, portfolio, price snapshot, round id, and trace id.  `FakeTradeGateway`
records `TradeCommand` values in memory and implements idempotency; it never
mutates real trading data.  `FakeEventSink` records the immutable Agent and
Tool lifecycle events for assertions.

The three assertion helpers return `None` on success and raise
`AssertionError` with a component-specific message on a contract violation.
They check DTO result types, context and trace ids, JSON schemas, namespaces,
timeouts/deadlines, capability declarations, event redaction, and synchronous
return values.  A coroutine or other awaitable returned from an Agent, Tool, or
Provider is rejected as an unsupported v1 implementation.

```python
from benchmark.testing import AgentCase, assert_agent_contract, build_fake_context
from benchmark.contracts import TerminationReason

assert_agent_contract(
    Factory(),
    [
        AgentCase(
            context=build_fake_context(account_id=7),
            expected_termination=TerminationReason.HOLD,
        )
    ],
)
```

Run the same checks from the command line with
`alpha-arena extension test path/to/extension`.  The command loads only the
selected catalog and never starts the application runtime.

When `assert_tool_contract()` is called without cases, it derives schema
examples and invokes every listed Tool.  If a derived example is not schema
valid, the helper fails and the caller must pass an explicit `ToolCase`.
`trading.write` is authorized only for `core.execute_trade`, matching the
production Tool registry.
