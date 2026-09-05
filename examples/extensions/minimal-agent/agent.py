from benchmark.contracts import AgentRunResult, DecisionContext, TerminationReason


class HoldAgent:
    def run(self, context: DecisionContext) -> AgentRunResult:
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=TerminationReason.HOLD,
            summary="Example Agent holds by default.",
        )


class HoldAgentFactory:
    def create(self, context, config):
        del context, config
        return HoldAgent()
