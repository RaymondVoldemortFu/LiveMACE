from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from benchmark.agents import AgentBuildContext
from benchmark.contracts import (
    AccountView,
    DecisionContext,
    PortfolioView,
    ToolResult,
)
from benchmark.tools import ToolInvoker


class RecordingEvents:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FakeToolInvoker:
    def __init__(self):
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return ToolResult(
            ok=False,
            error_code="TOOL_NOT_CONFIGURED",
            error_message="No Tool configured for this Agent runtime test",
        )


@pytest.fixture
def event_sink():
    return RecordingEvents()


@pytest.fixture
def tool_invoker():
    value = FakeToolInvoker()
    assert isinstance(value, ToolInvoker)
    return value


@pytest.fixture
def build_context(event_sink, tool_invoker):
    return AgentBuildContext(
        llm=object(),
        tools=tool_invoker,
        prompts=object(),
        events=event_sink,
    )


@pytest.fixture
def decision_context():
    now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    return DecisionContext(
        account_id=7,
        decision_round_id="round-7",
        trace_id="trace-7",
        portfolio=PortfolioView(
            account=AccountView(
                id=7,
                name="third-party-test",
                initial_capital=Decimal("10000"),
                current_cash=Decimal("10000"),
                frozen_cash=Decimal("0"),
                margin_used=Decimal("0"),
            ),
            positions=(),
            prices={"BTC": Decimal("50000")},
            total_assets=Decimal("10000"),
            captured_at=now,
        ),
        config={},
        started_at=now,
    )
