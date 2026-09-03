"""Fake DecisionContext and AgentBuildContext builders for contract tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from benchmark.agents import AgentBuildContext, NullEventSink
from benchmark.contracts import (
    AccountView,
    DecisionContext,
    PortfolioView,
    ToolContext,
)
from benchmark.prompts import PromptRegistry
from benchmark.testing.providers import FakeLLMClientPort
from benchmark.tools import ToolResult


class _EmptyToolInvoker:
    def call(self, name, arguments):
        return ToolResult(
            ok=False,
            error_code="TOOL_NOT_CONFIGURED",
            error_message=f"No Tool configured: {name}",
        )


def build_fake_context(
    *,
    account_id: int = 1,
    cash: str = "10000",
    trace_id: str | None = None,
    decision_round_id: str | None = None,
) -> DecisionContext:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cash_value = Decimal(cash)
    return DecisionContext(
        account_id=account_id,
        decision_round_id=decision_round_id or f"round-{uuid4().hex[:8]}",
        trace_id=trace_id or f"trace-{uuid4().hex[:8]}",
        portfolio=PortfolioView(
            account=AccountView(
                id=account_id,
                name="example-account",
                initial_capital=Decimal("10000"),
                current_cash=cash_value,
                frozen_cash=Decimal("0"),
                margin_used=Decimal("0"),
            ),
            positions=(),
            prices={"BTC": Decimal("50000")},
            total_assets=cash_value,
            captured_at=now,
        ),
        config={},
        started_at=now,
    )


def build_fake_build_context(
    *,
    llm: object | None = None,
    tools: object | None = None,
    prompts: object | None = None,
) -> AgentBuildContext:
    prompt_registry = prompts
    if prompt_registry is None:
        prompt_registry = PromptRegistry()
        prompt_registry.freeze()
    return AgentBuildContext(
        llm=llm or FakeLLMClientPort(),
        tools=tools or _EmptyToolInvoker(),
        prompts=prompt_registry,
        events=NullEventSink(),
    )


def tool_context_from_decision(context: DecisionContext) -> ToolContext:
    from benchmark.contracts import KNOWN_CAPABILITIES

    return ToolContext(
        account_id=context.account_id,
        decision_round_id=context.decision_round_id,
        trace_id=context.trace_id,
        call_id=f"call-{uuid4().hex[:8]}",
        capabilities=frozenset(KNOWN_CAPABILITIES),
    )


__all__ = [
    "build_fake_context",
    "build_fake_build_context",
    "tool_context_from_decision",
]
