"""
Trading Commands Service - Handles order execution and trading logic
"""
import logging
import random
import threading
import os
from decimal import Decimal
from typing import Dict, Optional, Tuple, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.connection import SessionLocal
from database.models import Position, Account
from services.asset_calculator import calc_positions_value
from services.market_data import get_last_price, get_market_status
from services.order_matching import create_order, check_and_execute_order
from services.order_executor_leverage import place_and_execute_crypto
from services.ai_decision_service import (
    save_ai_decision, 
    get_active_ai_accounts, 
    _get_portfolio_data,
    SUPPORTED_SYMBOLS,
    call_agent_for_decision
)
from services.baselines import BuyHoldBaseline, GridBaseline, is_baseline_trading_account
from config.agent_config import AgentConfig
from config.market_data_config import ALPACA_US_FEED_ENABLED
from services.alpaca_market_data import SUPPORTED_STOCKS as US_TRADING_SYMBOLS
from services.trading_symbols import AI_TRADING_SYMBOLS
from repositories.account_repo import get_account, list_active_ai_accounts
from repositories.position_repo import get_position
from services.tool_cache import tool_cache


logger = logging.getLogger(__name__)
trade_logger = logging.getLogger("trade_execution")
_ai_trade_run_lock = threading.Lock()
_baseline_trade_run_lock = threading.Lock()


_buy_hold_baseline = BuyHoldBaseline()
_grid_baseline = GridBaseline()

US_TRADING_SYMBOLS = list(US_TRADING_SYMBOLS)
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
            except Exception as e:
                logger.error(f"BuyHold baseline failed for account={account.id} ({account.name}): {e}", exc_info=True)
        elif agent_type == "grid":
            try:
                _grid_baseline.run_tick(db, account, prices)
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


def _collect_account_decision(
    account_id: int,
    prices: Dict[str, float],
    decision_round_id: Optional[str] = None,
) -> Optional[Dict]:
    """
    Collect agent decision for one account in an isolated DB session.
    This is safe to run in worker threads.
    """
    if _shutdown_requested():
        logger.info(
            "Scheduler shutdown requested; skipping decision collection for account %s",
            account_id,
        )
        return None
    db = SessionLocal()
    try:
        account = get_account(db, account_id)
        if not account:
            logger.warning(f"Account {account_id} not found while collecting decision")
            return None

        portfolio = _get_portfolio_data(db, account)
        if portfolio["total_assets"] <= 0:
            logger.debug(f"Account {account.name} has non-positive total assets, skip decision")
            return None

        decision = call_agent_for_decision(
            account,
            portfolio,
            prices,
            db,
            decision_round_id=decision_round_id,
        )

        if not decision or not isinstance(decision, dict):
            return None

        return {
            "account_id": account.id,
            "account_name": account.name,
            "portfolio": portfolio,
            "decision": decision,
        }
    except Exception as e:
        logger.error(f"Decision collection failed for account {account_id}: {e}", exc_info=True)
        return None
    finally:
        db.close()


def _process_account_decision_payload(db: Session, payload: Dict, prices: Dict[str, float]) -> None:
    """Persist one completed Agent decision without executing legacy JSON orders."""
    account = get_account(db, payload["account_id"])
    if not account:
        logger.warning(f"Account {payload['account_id']} disappeared before decision logging")
        return

    if is_baseline_trading_account(account):
        return

    account_agent_type = str(getattr(account, "agent_type", "react") or "react").strip().lower()
    if account_agent_type not in AGENT_DECISION_TYPES:
        return

    portfolio = payload["portfolio"]
    decision = payload["decision"]
    if not decision or not isinstance(decision, dict):
        logger.warning(f"Invalid decision payload for account {account.name}, skipping")
        return

    executed = bool(decision.get("executed"))
    order_id = decision.get("order_id")
    execution_price = decision.get("execution_price")
    execution_quantity = decision.get("execution_quantity")

    normalized_order_id = None
    if order_id is not None:
        try:
            parsed_order_id = int(order_id)
        except (TypeError, ValueError):
            parsed_order_id = None
        if parsed_order_id is not None and parsed_order_id > 0:
            normalized_order_id = parsed_order_id

    try:
        save_ai_decision(
            db,
            account.id,
            decision,
            portfolio,
            executed=executed,
            order_id=normalized_order_id,
            execution_price=float(execution_price) if execution_price is not None else None,
            execution_quantity=float(execution_quantity) if execution_quantity is not None else None,
        )
    except Exception as account_err:
        logger.error(f"AI decision logging failed for account {account.name}: {account_err}", exc_info=True)


def place_ai_driven_crypto_order(max_ratio: float = 0.2) -> None:
    """Place crypto order based on AI model decision for all active accounts"""
    if not _ai_trade_run_lock.acquire(blocking=False):
        logger.warning("AI trading loop is already running; skip this trigger to avoid overlap")
        return

    db = None
    try:
        db = SessionLocal()
        accounts = _load_trading_accounts(db)
        if not accounts:
            logger.debug("No available accounts, skipping AI trading")
            return

        # Get latest market prices once for all accounts
        prices = {}
        prices.update(_get_market_prices(AI_TRADING_SYMBOLS, "CRYPTO"))
        prices.update(_get_market_prices(US_TRADING_SYMBOLS, "US"))
        if not prices:
            logger.warning("Failed to fetch market prices, skipping AI trading")
            return

        # Collect and process account decisions concurrently.
        # Each completed decision is handled immediately (no end-of-batch cache).
        concurrency = min(
            len(accounts),
            max(1, int(getattr(AgentConfig, "AGENT_MAX_CONCURRENCY", 1))),
        )

        decision_round_id = tool_cache.create_round_id(scope="ai_trade")
        logger.info(f"Started AI trading decision round: {decision_round_id}")

        agent_accounts = [
            a
            for a in accounts
            if str(getattr(a, "agent_type", "react") or "react").strip().lower() in AGENT_DECISION_TYPES
            and not is_baseline_trading_account(a)
        ]
        if agent_accounts:
            with ThreadPoolExecutor(max_workers=min(concurrency, len(agent_accounts))) as executor:
                future_map = {
                    executor.submit(
                        _collect_account_decision,
                        account.id,
                        prices,
                        decision_round_id,
                    ): account.id
                    for account in agent_accounts
                }
                for fut in as_completed(future_map):
                    account_id = future_map[fut]
                    if _shutdown_requested():
                        logger.info(
                            "Scheduler shutdown requested; cancelling remaining AI decision workers"
                        )
                        for pending in future_map:
                            pending.cancel()
                        break
                    try:
                        result = fut.result()
                        if result:
                            _process_account_decision_payload(db, result, prices)
                    except Exception as worker_err:
                        logger.error(
                            f"Decision worker crashed for account_id={account_id}: {worker_err}",
                            exc_info=True,
                        )

        # Clear failed-transaction state from agent workers before baseline DB writes.
        try:
            db.rollback()
        except Exception:
            pass

    except Exception as err:
        logger.error(f"AI-driven order placement failed: {err}", exc_info=True)
        if db is not None:
            db.rollback()
    finally:
        if db is not None:
            db.close()
        _ai_trade_run_lock.release()


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
