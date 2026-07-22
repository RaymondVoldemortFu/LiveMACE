"""Seed bootstrap stage (M18): default trading configs, user and account.

Logic moved verbatim from ``main.py``'s ``on_startup``; idempotent, and
each step records what it actually did.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class SeedReport:
    trading_configs_seeded: bool = False
    default_user_created: bool = False
    default_account_created: bool = False


def ensure_default_trading_configs(db: Session) -> bool:
    from config.settings import DEFAULT_TRADING_CONFIGS
    from database.models import TradingConfig

    if db.query(TradingConfig).count() != 0:
        return False
    for cfg in DEFAULT_TRADING_CONFIGS.values():
        db.add(
            TradingConfig(
                version="v1",
                market=cfg.market,
                min_commission=cfg.min_commission,
                commission_rate=cfg.commission_rate,
                exchange_rate=cfg.exchange_rate,
                min_order_quantity=cfg.min_order_quantity,
                lot_size=cfg.lot_size,
            )
        )
    db.commit()
    return True


def ensure_default_user(db: Session):
    from database.models import User

    default_user = db.query(User).filter(User.username == "default").first()
    created = False
    if not default_user:
        default_user = User(
            username="default",
            email=None,
            password_hash=None,
            is_active="true",
        )
        db.add(default_user)
        db.commit()
        db.refresh(default_user)
        created = True
    return default_user, created


def ensure_default_account(db: Session, user_id: int) -> bool:
    from database.models import Account

    default_accounts = db.query(Account).filter(Account.user_id == user_id).all()
    if default_accounts:
        return False
    db.add(
        Account(
            user_id=user_id,
            version="v1",
            name="GPT",
            account_type="AI",
            tool_routing_enabled="true",
            model=None,
            base_url=None,
            api_key=None,
            initial_capital=10000.0,
            current_cash=10000.0,
            frozen_cash=0.0,
            is_active="true",
        )
    )
    db.commit()
    return True


def run_seed_bootstrap(session_factory: Optional[Callable[[], Session]] = None) -> SeedReport:
    if session_factory is None:
        from database.connection import SessionLocal as session_factory

    report = SeedReport()
    db = session_factory()
    try:
        report.trading_configs_seeded = ensure_default_trading_configs(db)
        default_user, report.default_user_created = ensure_default_user(db)
        report.default_account_created = ensure_default_account(db, default_user.id)
    finally:
        db.close()
    logger.info(
        "seed bootstrap: configs_seeded=%s user_created=%s account_created=%s",
        report.trading_configs_seeded,
        report.default_user_created,
        report.default_account_created,
    )
    return report
