"""
Trading Commands Service - Handles order execution and trading logic
"""
import logging
import random
import threading
import os
from decimal import Decimal
from typing import Dict, Optional, Tuple, List
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.connection import SessionLocal
from database.models import Account
from services.asset_calculator import calc_positions_value
from services.market_data import get_trading_price as get_last_price
from services.order_matching import create_order, check_and_execute_order
from services.ai_decision_service import (
    get_active_ai_accounts, 
    SUPPORTED_SYMBOLS
)
from services.baselines import BuyHoldBaseline, GridBaseline, is_baseline_trading_account
from benchmark.infrastructure.market.symbols import CRYPTO_SYMBOLS, US_SYMBOLS
from config.agent_config import AgentConfig
from config.market_data_config import ALPACA_US_FEED_ENABLED
from repositories.account_repo import list_active_ai_accounts
from repositories.position_repo import get_position


logger = logging.getLogger(__name__)
trade_logger = logging.getLogger("trade_execution")
_ai_trade_run_lock = threading.Lock()
_baseline_trade_run_lock = threading.Lock()


_buy_hold_baseline = BuyHoldBaseline()
_grid_baseline = GridBaseline()

US_TRADING_SYMBOLS = list(US_SYMBOLS)
AI_TRADING_SYMBOLS = list(CRYPTO_SYMBOLS)
AGENT_DECISION_TYPES = {"react", "multi_agent", "advanced_multi_agent", "rule_aware"}
_baseline_us_feed_skip_logged = False


def _infer_market(symbol: str, decision_market: Optional[str]) -> str:
    if decision_market:
        return str(decision_market).strip().upper()
    symbol_norm = str(symbol or "").strip().upper()
    if symbol_norm in US_TRADING_SYMBOLS:
        return "US"
    return "CRYPTO"


def _validate_decision_symbol_market(symbol: str, market: str) -> None:
    symbol_norm = str(symbol or "").strip().upper()
    market_norm = str(market or "").strip().upper()
    if market_norm == "US" and symbol_norm not in US_TRADING_SYMBOLS:
        raise ValueError(f"Invalid decision params: symbol '{symbol_norm}' is not a supported US stock")
    if market_norm == "CRYPTO" and symbol_norm in US_TRADING_SYMBOLS:
        raise ValueError(
            f"Invalid decision params: symbol '{symbol_norm}' is US stock but decision market is CRYPTO"
        )


def _log_trade_execution(operation: str, symbol: str, target_portion: float, price: float, leverage: int, executed: bool, reason: str = ""):
    """Log trade execution details to specific trade logger"""
    status = "Yes" if executed else "No"
    op_str = operation.upper()
    price_str = f"${price:.2f}" if price else "N/A"
    
    # Format: ACTION SYMBOL PORTION PRICE LEVERAGE EXECUTED - REASON
    # Matching user example: CLOSE BTC 28.66% ... 
    msg = f"{op_str:<6} {symbol:<5} {target_portion:.2%} {price_str} {leverage}x {status}"
    
    if not executed:
        msg += f" - Reason: {reason}"
        
    trade_logger.info(msg)


def _get_market_prices(symbols: List[str], market: str, suppress_symbol_warnings: bool = False) -> Dict[str, float]:
    """Get latest prices for given symbols"""
    prices = {}
    failed_symbols: List[str] = []
    non_positive_symbols: List[str] = []
    exception_symbols: List[str] = []
    for symbol in symbols:
        if _shutdown_requested():
            break
        try:
            price = float(get_last_price(symbol, market))
            if price > 0:
                prices[symbol] = price
            else:
                failed_symbols.append(symbol)
                non_positive_symbols.append(symbol)
                if not suppress_symbol_warnings:
                    logger.warning(
                        "Non-positive price for %s (%s): %s",
                        symbol,
                        market,
                        price,
                    )
        except Exception as err:
            failed_symbols.append(symbol)
            exception_symbols.append(symbol)
            if not suppress_symbol_warnings:
                logger.warning(f"Failed to get price for {symbol}: {err}")

    if suppress_symbol_warnings and failed_symbols:
        logger.warning(
            (
                "Failed to get %s prices for %d/%d symbols "
                "(non_positive=%d, exceptions=%d; suppressed per-symbol warnings). Sample: %s"
            ),
            market,
            len(failed_symbols),
            len(symbols),
            len(non_positive_symbols),
            len(exception_symbols),
            ", ".join(failed_symbols[:5]),
        )
    return prices


def _baseline_should_fetch_us_prices() -> bool:
    if not ALPACA_US_FEED_ENABLED:
        return False
    alpaca_key = (os.getenv("ALPACA_KEY") or "").strip()
    alpaca_secret = (os.getenv("ALPACA_SECRET") or "").strip()
    return bool(alpaca_key and alpaca_secret)


def _get_active_ai_trading_accounts(db: Session) -> List[Account]:
    return list_active_ai_accounts(db)


def _load_trading_accounts(db: Session) -> List[Account]:
    active_accounts = _get_active_ai_trading_accounts(db)
    agent_accounts = get_active_ai_accounts(db)
    baseline_accounts = [account for account in active_accounts if is_baseline_trading_account(account)]
    accounts_by_id = {account.id: account for account in agent_accounts}
    for account in baseline_accounts:
        accounts_by_id.setdefault(account.id, account)
    return list(accounts_by_id.values())


def _load_baseline_accounts(db: Session) -> List[Account]:
    active_accounts = _get_active_ai_trading_accounts(db)
    return [
        account
        for account in active_accounts
        if (getattr(account, "agent_type", "react") or "react").strip().lower() in {"buy_hold", "grid"}
    ]


def _shutdown_requested() -> bool:
    """Cooperative cancellation checkpoint (scheduler shutdown in progress)."""
    from services.scheduler import shutdown_cancellation_requested

    return shutdown_cancellation_requested()


def _run_baseline_accounts(db: Session, accounts: List[Account], prices: Dict[str, float]) -> None:
    now = datetime.now(timezone.utc)
    for account in accounts:
        if _shutdown_requested():
            logger.info("Scheduler shutdown requested; stopping baseline trading loop early")
            return
        agent_type = getattr(account, "agent_type", "react") or "react"
        agent_type = str(agent_type).strip().lower()
        if agent_type == "buy_hold":
            try:
                _buy_hold_baseline.run_tick(db, account, prices, now=now)
                logger.info("Baseline tick returned: account=%s agent=buy_hold", account.id)
            except Exception as e:
                logger.error(f"BuyHold baseline failed for account={account.id} ({account.name}): {e}", exc_info=True)
        elif agent_type == "grid":
            try:
                _grid_baseline.run_tick(db, account, prices)
                logger.info("Baseline tick returned: account=%s agent=grid", account.id)
            except Exception as e:
                logger.error(f"Grid baseline failed for account={account.id} ({account.name}): {e}", exc_info=True)


def _select_side(db: Session, account: Account, symbol: str, max_value: float) -> Optional[Tuple[str, int]]:
    """Select random trading side and quantity for legacy random trading"""
    market = "CRYPTO"
    try:
        price = float(get_last_price(symbol, market))
    except Exception as err:
        logger.warning("Cannot get price for %s: %s", symbol, err)
        return None

    if price <= 0:
        logger.debug("%s returned non-positive price %s", symbol, price)
        return None

    max_quantity_by_value = int(Decimal(str(max_value)) // Decimal(str(price)))
    position = get_position(db, account.id, symbol, market)
    available_quantity = int(position.available_quantity) if position else 0

    choices = []

    if float(account.current_cash) >= price and max_quantity_by_value >= 1:
        choices.append(("BUY", max_quantity_by_value))

    if available_quantity > 0:
        max_sell_quantity = min(available_quantity, max_quantity_by_value if max_quantity_by_value >= 1 else available_quantity)
        if max_sell_quantity >= 1:
            choices.append(("SELL", max_sell_quantity))

    if not choices:
        return None

    side, max_qty = random.choice(choices)
    quantity = random.randint(1, max_qty)
    return side, quantity


def place_ai_driven_crypto_order(max_ratio: float = 0.2):
    """Scheduler facade for the synchronous decision application service."""
    from benchmark.application.decisions.service import DecisionRoundService, RunDecisionRound
    result = DecisionRoundService().run(RunDecisionRound(
        account_ids=None, max_concurrency=AgentConfig.AGENT_MAX_CONCURRENCY, trigger="scheduler"))
    logger.info("Decision round %s completed: accounts=%s errors=%s",
                result.decision_round_id, result.processed_accounts, dict(result.errors))
    return result


def place_baseline_driven_order() -> None:
    """Run baseline strategies (buy_hold/grid) independently from AI decision schedule."""
    if not _baseline_trade_run_lock.acquire(blocking=False):
        logger.warning("Baseline trading loop is already running; skip this trigger to avoid overlap")
        return

    db = None
    try:
        db = SessionLocal()
        baseline_accounts = _load_baseline_accounts(db)
        if not baseline_accounts:
            logger.debug("No baseline accounts, skipping baseline trading")
            return

        global _baseline_us_feed_skip_logged

        prices = {}
        prices.update(_get_market_prices(AI_TRADING_SYMBOLS, "CRYPTO"))

        if _baseline_should_fetch_us_prices():
            prices.update(_get_market_prices(US_TRADING_SYMBOLS, "US", suppress_symbol_warnings=True))
            _baseline_us_feed_skip_logged = False
        elif not _baseline_us_feed_skip_logged:
            logger.warning(
                "Baseline US price fetch disabled (Alpaca feed disabled or missing credentials); skipping US symbols"
            )
            _baseline_us_feed_skip_logged = True

        if not prices:
            logger.warning("Failed to fetch market prices, skipping baseline trading")
            return

        _run_baseline_accounts(db, baseline_accounts, prices)

    except Exception as err:
        logger.error(f"Baseline-driven order placement failed: {err}", exc_info=True)
        if db is not None:
            db.rollback()
    finally:
        if db is not None:
            db.close()
        _baseline_trade_run_lock.release()


def place_random_crypto_order(max_ratio: float = 0.2) -> None:
    """Legacy random order placement (kept for backward compatibility)"""
    db = SessionLocal()
    try:
        accounts = get_active_ai_accounts(db)
        if not accounts:
            logger.debug("No available accounts, skipping auto order placement")
            return
        
        # For legacy compatibility, just pick a random account from the list
        account = random.choice(accounts)

        positions_value = calc_positions_value(db, account.id)
        total_assets = positions_value + float(account.current_cash)

        if total_assets <= 0:
            logger.debug("Account %s total assets non-positive, skipping auto order placement", account.name)
            return

        max_order_value = total_assets * max_ratio
        if max_order_value <= 0:
            logger.debug("Account %s maximum order amount is 0, skipping", account.name)
            return

        symbol = random.choice(list(SUPPORTED_SYMBOLS.keys()))
        side_info = _select_side(db, account, symbol, max_order_value)
        if not side_info:
            logger.debug("Account %s has no executable direction for %s, skipping", account.name, symbol)
            return

        side, quantity = side_info
        name = SUPPORTED_SYMBOLS[symbol]

        order = create_order(
            db=db,
            account=account,
            symbol=symbol,
            name=name,
            side=side,
            order_type="MARKET",
            price=None,
            quantity=quantity,
        )

        db.commit()
        db.refresh(order)

        executed = check_and_execute_order(db, order)
        if executed:
            db.refresh(order)
            logger.info("Auto order executed: account=%s %s %s %s quantity=%s", account.name, side, symbol, order.order_no, quantity)
        else:
            logger.info("Auto order created: account=%s %s %s quantity=%s order_id=%s", account.name, side, symbol, quantity, order.order_no)

    except Exception as err:
        logger.error("Auto order placement failed: %s", err)
        db.rollback()
    finally:
        db.close()


AUTO_TRADE_JOB_ID = "auto_crypto_trade"
AI_TRADE_JOB_ID = "ai_crypto_trade"
BASELINE_TRADE_JOB_ID = "baseline_trade"
