"""Materialize immutable account snapshots before invoking external services."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from benchmark.accounts.config import AccountExtensionConfig, config_from_legacy_account
from benchmark.accounts.service import get_runtime_config
from benchmark.contracts import (
    AccountView,
    DecisionContext,
    Market,
    PortfolioView,
    PositionView,
)
from benchmark.persistence.uow import SqlAlchemyUnitOfWork
from database.connection import SessionLocal
from services.security.api_key_security import resolve_runtime_api_key


@dataclass(frozen=True)
class WorkerInput:
    context: DecisionContext
    config: AccountExtensionConfig
    model: str
    memory_enabled: bool
    tool_routing_enabled: bool
    api_key: str = field(repr=False)
    base_url: str = field(repr=False)


def build_decision_trace_id(account, decision_round_id):
    return str(uuid4())


def load_worker_input(account_id, prices, round_id, *, session_factory=SessionLocal):
    with SqlAlchemyUnitOfWork(session_factory) as uow:
        account = uow.accounts.get(account_id)
        if account is None:
            raise ValueError("Account no longer exists")
        stored = get_runtime_config(uow, account_id)
        if stored is not None and not stored.valid:
            raise ValueError("Account runtime configuration is invalid")
        config = stored.config if stored else config_from_legacy_account(account)
        positions = tuple(
            PositionView(
                p.symbol,
                Market(p.market),
                Decimal(p.quantity),
                Decimal(p.available_quantity),
                Decimal(p.avg_cost),
                p.leverage,
                p.side,
            )
            for p in uow.positions.list_by_account(account_id)
            if p.quantity != 0
        )
        view = AccountView(
            account.id,
            account.name,
            Decimal(account.initial_capital),
            Decimal(account.current_cash),
            Decimal(account.frozen_cash),
            Decimal(account.margin_used),
        )
        # Use the established position-equity calculator with the collected prices;
        # no network calls or ORM entities cross the read transaction boundary.
        from services.asset_calculator import calculate_position_market_value

        total = view.current_cash
        for p in positions:
            price = prices.get(p.symbol)
            if price is None or price <= 0:
                raise ValueError(f"Missing valuation price for {p.symbol}")
            total += Decimal(str(calculate_position_market_value(p, price)))
        now = datetime.now(timezone.utc)
        portfolio = PortfolioView(
            view, positions, {k: Decimal(str(v)) for k, v in prices.items()}, total, now
        )
        context = DecisionContext(
            account_id, round_id, str(uuid4()), portfolio, config.to_dict(), now
        )
        return WorkerInput(
            context,
            config,
            account.model,
            str(account.memory_enabled).lower() == "true",
            str(account.tool_routing_enabled).lower() == "true",
            resolve_runtime_api_key(account.api_key),
            account.base_url,
        )
