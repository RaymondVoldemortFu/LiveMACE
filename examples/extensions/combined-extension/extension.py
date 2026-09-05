from benchmark.contracts import AgentRunResult, DecisionContext, TerminationReason
from benchmark.contracts import MARKET_READ, SideEffect, ToolResult, ToolSpec


class HoldAgent:
    def run(self, context: DecisionContext) -> AgentRunResult:
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=TerminationReason.HOLD,
            summary="Combined example holds.",
        )


class HoldAgentFactory:
    def create(self, context, config):
        del context, config
        return HoldAgent()


class StatusTool:
    def __init__(self):
        self._spec = ToolSpec(
            name="com.example.combined.status",
            description="Report that the combined extension is loaded.",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            side_effect=SideEffect.READ_ONLY,
            required_capabilities=(MARKET_READ,),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, context, arguments) -> ToolResult:
        del arguments
        return ToolResult(
            ok=True,
            value={"account_id": context.account_id, "status": "loaded"},
        )


class StatusToolProvider:
    def list_tools(self):
        return (StatusTool(),)
