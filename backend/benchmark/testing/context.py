"""Deterministic public DTO fixtures for extension tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping
from uuid import uuid4

from benchmark.agents import AgentBuildContext
from benchmark.contracts import (
    KNOWN_CAPABILITIES,
    AccountView,
    DecisionContext,
    Market,
    PortfolioView,
    PositionView,
    ToolContext,
    to_jsonable,
)
from benchmark.prompts import PromptRegistry
from benchmark.providers import LLMResponse
from benchmark.tools import SynchronousToolInvoker, ToolInvoker, ToolRegistry

from .events import FakeEventSink
from .providers import FakeLLMClientPort


_DEFAULT_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


def _decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    else:
        try:
            result = Decimal(str(value))
        except Exception as exc:  # pragma: no cover - defensive fixture error
            raise TypeError(f"{field_name} must be Decimal-compatible") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return result


def _market(value: Any) -> Market:
    if isinstance(value, Market):
        return value
    if isinstance(value, str):
        try:
            return Market[value.upper()]
        except KeyError:
            try:
                return Market(value.upper())
            except ValueError:
                pass
    raise TypeError("market must be Market or a known market name")


def _position(value: PositionView | Mapping[str, Any]) -> PositionView:
    if isinstance(value, PositionView):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("positions must contain PositionView or mappings")
    return PositionView(
        symbol=str(value.get("symbol", "BTC")),
        market=_market(value.get("market", Market.CRYPTO)),
        quantity=_decimal(value.get("quantity", "0"), "quantity"),
        available_quantity=_decimal(
            value.get("available_quantity", value.get("quantity", "0")),
            "available_quantity",
        ),
        avg_cost=_decimal(value.get("avg_cost", "0"), "avg_cost"),
        leverage=int(value.get("leverage", 1)),
        side=value.get("side"),
    )


def _portfolio_from_mapping(
    value: Mapping[str, Any], *, account_id: int, now: datetime
) -> PortfolioView:
    account_value = value.get("account", {})
    if isinstance(account_value, AccountView):
        account = account_value
    elif isinstance(account_value, Mapping):
        initial = _decimal(
            account_value.get("initial_capital", "10000"), "initial_capital"
        )
        account = AccountView(
            id=account_id,
            name=str(account_value.get("name", "fake-account")),
            initial_capital=initial,
            current_cash=_decimal(
                account_value.get("current_cash", initial), "current_cash"
            ),
            frozen_cash=_decimal(account_value.get("frozen_cash", "0"), "frozen_cash"),
            margin_used=_decimal(account_value.get("margin_used", "0"), "margin_used"),
        )
    else:
        raise TypeError("portfolio.account must be AccountView or a mapping")
    raw_prices = value.get("prices", {"BTC": "100"})
    if not isinstance(raw_prices, Mapping):
        raise TypeError("portfolio.prices must be a mapping")
    prices = {str(key): _decimal(item, "price") for key, item in raw_prices.items()}
    return PortfolioView(
        account=account,
        positions=tuple(_position(item) for item in value.get("positions", ())),
        prices=prices,
        total_assets=_decimal(
            value.get("total_assets", account.current_cash), "total_assets"
        ),
        captured_at=value.get("captured_at", now),
    )


def build_fake_context(
    account_id: int = 1,
    decision_round_id: str | None = None,
    trace_id: str | None = None,
    portfolio: PortfolioView | Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
    started_at: datetime | None = None,
    *,
    account_name: str = "fake-account",
    initial_capital: Decimal | int | float | str = Decimal("10000"),
    current_cash: Decimal | int | float | str | None = None,
    frozen_cash: Decimal | int | float | str = Decimal("0"),
    margin_used: Decimal | int | float | str = Decimal("0"),
    positions: Iterable[PositionView | Mapping[str, Any]] = (),
    prices: Mapping[str, Decimal | int | float | str] | None = None,
    total_assets: Decimal | int | float | str | None = None,
    captured_at: datetime | None = None,
    now: datetime | None = None,
    cash: Decimal | int | float | str | None = None,
) -> DecisionContext:
    """Build a valid, immutable ``DecisionContext`` without application state.

    Defaults are deterministic so contract tests can compare serialized
    contexts.  Every optional numeric input accepts a Decimal-compatible value
    and is converted before constructing the public DTO.
    """

    if cash is not None:
        if current_cash is not None:
            raise TypeError("cash and current_cash are aliases; provide one")
        current_cash = cash
    if (
        not isinstance(account_id, int)
        or isinstance(account_id, bool)
        or account_id <= 0
    ):
        raise ValueError("account_id must be a positive integer")
    current_time = now or started_at or _DEFAULT_TIME
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now/started_at must be timezone-aware")
    started = started_at or current_time
    round_id = (
        decision_round_id
        if decision_round_id is not None
        else f"fake-round-{account_id}"
    )
    trace = trace_id if trace_id is not None else f"fake-trace-{account_id}"

    if portfolio is None:
        initial = _decimal(initial_capital, "initial_capital")
        cash = (
            initial if current_cash is None else _decimal(current_cash, "current_cash")
        )
        raw_prices = prices if prices is not None else {"BTC": Decimal("100")}
        normalized_prices = {
            str(symbol): _decimal(price, "price")
            for symbol, price in raw_prices.items()
        }
        captured = captured_at or current_time
        snapshot = PortfolioView(
            account=AccountView(
                id=account_id,
                name=account_name,
                initial_capital=initial,
                current_cash=cash,
                frozen_cash=_decimal(frozen_cash, "frozen_cash"),
                margin_used=_decimal(margin_used, "margin_used"),
            ),
            positions=tuple(_position(item) for item in positions),
            prices=normalized_prices,
            total_assets=(
                cash if total_assets is None else _decimal(total_assets, "total_assets")
            ),
            captured_at=captured,
        )
    elif isinstance(portfolio, PortfolioView):
        snapshot = portfolio
    elif isinstance(portfolio, Mapping):
        snapshot = _portfolio_from_mapping(
            portfolio, account_id=account_id, now=current_time
        )
    else:
        raise TypeError("portfolio must be PortfolioView, mapping, or None")

    # ``DecisionContext`` performs the final JSON and immutability checks.
    normalized_config = {} if config is None else to_jsonable(config)
    if not isinstance(normalized_config, dict):
        raise TypeError("config must be a JSON object")
    return DecisionContext(
        account_id=account_id,
        decision_round_id=round_id,
        trace_id=trace,
        portfolio=snapshot,
        config=normalized_config,
        started_at=started,
    )


def build_fake_agent_build_context(
    *,
    llm: Any | None = None,
    tools: ToolInvoker | ToolRegistry | None = None,
    prompts: Any | None = None,
    events: Any | None = None,
    account_id: int = 1,
    decision_round_id: str = "fake-round-1",
    trace_id: str = "fake-trace-1",
    deadline_at: datetime | None = None,
) -> AgentBuildContext:
    """Build an ``AgentBuildContext`` backed solely by public test doubles."""

    llm_port = (
        llm if llm is not None else FakeLLMClientPort((LLMResponse(content=""),) * 8)
    )
    if tools is None:
        registry = ToolRegistry()
        registry.freeze()
        tool_port: ToolInvoker = SynchronousToolInvoker(
            registry,
            account_id=account_id,
            decision_round_id=decision_round_id,
            trace_id=trace_id,
            capabilities=frozenset(KNOWN_CAPABILITIES),
            deadline_at=deadline_at,
        )
    elif isinstance(tools, ToolRegistry):
        if not tools.frozen:
            tools.freeze()
        tool_port = SynchronousToolInvoker(
            tools,
            account_id=account_id,
            decision_round_id=decision_round_id,
            trace_id=trace_id,
            capabilities=frozenset(KNOWN_CAPABILITIES),
            deadline_at=deadline_at,
        )
    else:
        tool_port = tools
    prompt_port = prompts
    if prompt_port is None:
        prompt_port = PromptRegistry()
        prompt_port.freeze()
    event_port = events if events is not None else FakeEventSink()
    return AgentBuildContext(
        llm=llm_port,
        tools=tool_port,
        prompts=prompt_port,
        events=event_port,
    )


# A shorter name reads naturally in examples and remains explicit about the
# distinction from the DecisionContext fixture.
build_fake_build_context = build_fake_agent_build_context


def tool_context_from_decision(context: DecisionContext) -> ToolContext:
    """Create a fully authorized Tool context preserving decision identities."""

    if not isinstance(context, DecisionContext):
        raise TypeError("context must be DecisionContext")
    return ToolContext(
        account_id=context.account_id,
        decision_round_id=context.decision_round_id,
        trace_id=context.trace_id,
        call_id=f"call-{uuid4().hex[:8]}",
        capabilities=frozenset(KNOWN_CAPABILITIES),
    )


__all__ = [
    "build_fake_context",
    "build_fake_agent_build_context",
    "build_fake_build_context",
    "tool_context_from_decision",
]
