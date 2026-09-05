# Create an Agent in Ten Minutes

An Agent directory needs a manifest, a JSON config schema, and a synchronous
factory.  The factory receives an `AgentBuildContext`; the Agent receives a
read-only `DecisionContext` for one decision round.

```python
from typing import Mapping

from benchmark.agents import AgentBuildContext
from benchmark.contracts import AgentRunResult, DecisionContext, TerminationReason


class Factory:
    def create(self, context: AgentBuildContext, config: Mapping[str, object]):
        return HoldAgent(config.get("message", "holding"))


class HoldAgent:
    def __init__(self, message: object):
        self.message = str(message)

    def run(self, context: DecisionContext) -> AgentRunResult:
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=TerminationReason.HOLD,
            summary=self.message,
        )
```

Declare the factory in `alpha-arena-extension.yaml`:

```yaml
api_version: 1
id: com.example.hold-agent
version: 1.0.0
name: Hold Agent
python:
  requires: ">=3.10"
  entrypoint: agent:Factory
components:
  agents:
    - id: com.example.hold-agent.agent
      factory: agent:Factory
      config_schema: config.json
```

Use only the public `benchmark.*` imports shown above.  A run must return a
complete `AgentRunResult` before `run()` returns.  Trading, when needed, is a
Tool call; an Agent should not construct orders directly.

Run the static and runtime checks:

```bash
alpha-arena extension validate path/to/hold-agent
alpha-arena extension test path/to/hold-agent
```

The SPI is synchronous.  An Agent may manage its own event loop or worker pool
inside its synchronous `run()` implementation, but the framework does not
provide asynchronous compatibility, cancellation of those resources, or
correctness guarantees for them.
