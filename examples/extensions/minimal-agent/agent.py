"""A minimal synchronous Agent extension."""

from __future__ import annotations

from typing import Mapping

from benchmark.agents import AgentBuildContext
from benchmark.contracts import AgentRunResult, DecisionContext, TerminationReason


class MinimalAgent:
    def __init__(self, summary: str) -> None:
        self._summary = summary

    def run(self, context: DecisionContext) -> AgentRunResult:
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=TerminationReason.HOLD,
            summary=self._summary,
        )


class MinimalAgentFactory:
    def create(
        self,
        context: AgentBuildContext,
        config: Mapping[str, object],
    ) -> MinimalAgent:
        del context
        summary = config.get("summary", "No trade requested by the minimal Agent.")
        if not isinstance(summary, str):
            raise TypeError("summary must be a string")
        return MinimalAgent(summary)
