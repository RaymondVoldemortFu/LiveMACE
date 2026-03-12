from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from database.models import Account, Order, Position
from services.asset_calculator import calc_positions_value
from services.market_data import get_market_status
from services.alpaca_market_data import SUPPORTED_STOCKS
from services.order_matching import (
    cancel_order,
    check_and_execute_order,
    create_order,
    process_all_pending_orders,
)
from services.trading_symbols import AI_TRADING_SYMBOLS

logger = logging.getLogger(__name__)


CRYPTO_UNIVERSE: List[str] = list(AI_TRADING_SYMBOLS)
US_UNIVERSE: List[str] = list(SUPPORTED_STOCKS)
BASELINE_UNIVERSE: List[str] = list(CRYPTO_UNIVERSE) + list(US_UNIVERSE)


def _get_portfolio_data(db: Session, account: Account) -> Dict:
    positions = (
        db.query(Position)
        .filter(Position.account_id == account.id)
        .all()
    )

    portfolio: Dict[str, Dict] = {}
    for pos in positions:
        if float(pos.quantity) > 0:
            portfolio[pos.symbol] = {
                "quantity": float(pos.quantity),
                "avg_cost": float(pos.avg_cost),
                "current_value": float(pos.quantity) * float(pos.avg_cost),
                "side": (pos.side or "LONG").upper(),
                "leverage": pos.leverage,
                "market": pos.market,
            }

    return {
        "cash": float(account.current_cash),
        "frozen_cash": float(account.frozen_cash),
        "positions": portfolio,
        "total_assets": float(account.current_cash) + calc_positions_value(db, account.id),
    }


def _infer_market(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    if sym in US_UNIVERSE:
        return "US"
    return "CRYPTO"


def _is_trading_open(symbol: str, market: str) -> bool:
    try:
        status = get_market_status(symbol, market)
        return bool(status.get("is_trading", False))
    except Exception:
        # If market status cannot be retrieved, be conservative for US and permissive for crypto.
        return market != "US"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(str(raw).strip())
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(str(raw).strip())
    except Exception:
        return float(default)


def _align_period_end(now: datetime, interval_seconds: int) -> datetime:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0")
    ts = int(now.timestamp())
    aligned = ts - (ts % interval_seconds)
    return datetime.fromtimestamp(aligned, tz=timezone.utc)


@dataclass(frozen=True)
class BuyHoldConfig:
    universe: List[str]
    rebalance_seconds: int
    capital_usage: float
    min_trade_usd: float


def get_buy_hold_config() -> BuyHoldConfig:
    return BuyHoldConfig(
        universe=list(BASELINE_UNIVERSE),
        rebalance_seconds=max(60, _env_int("BUY_HOLD_REBALANCE_SECONDS", 3600)),
        capital_usage=min(1.0, max(0.0, _env_float("BUY_HOLD_CAPITAL_USAGE", 0.95))),
        min_trade_usd=max(0.0, _env_float("BUY_HOLD_MIN_TRADE_USD", 5.0)),
    )


@dataclass(frozen=True)
class GridConfig:
    universe: List[str]
    levels: int
    step_pct: float
    capital_usage: float
    min_order_usd: float
    max_pending_per_symbol: int
    cleanup_band_mult: float


def get_grid_config() -> GridConfig:
    return GridConfig(
        universe=list(BASELINE_UNIVERSE),
        levels=max(1, _env_int("GRID_LEVELS", 5)),
        step_pct=min(0.5, max(0.0001, _env_float("GRID_STEP_PCT", 0.01))),
        capital_usage=min(1.0, max(0.0, _env_float("GRID_CAPITAL_USAGE", 0.8))),
        min_order_usd=max(0.0, _env_float("GRID_MIN_ORDER_USD", 5.0)),
        max_pending_per_symbol=max(2, _env_int("GRID_MAX_PENDING_PER_SYMBOL", 24)),
        cleanup_band_mult=max(1.0, _env_float("GRID_CLEANUP_BAND_MULT", 1.5)),
    )


class BuyHoldBaseline:
    """Equal-weight buy & hold basket with periodic rebalance (long-only)."""

    def __init__(self, config: Optional[BuyHoldConfig] = None):
        self.config = config or get_buy_hold_config()
        self._last_rebalance_end: Dict[int, datetime] = {}

    def run_tick(self, db: Session, account: Account, prices: Dict[str, float], now: Optional[datetime] = None) -> None:
        now = now or _now_utc()
        period_end = _align_period_end(now, self.config.rebalance_seconds)

        last = self._last_rebalance_end.get(account.id)
        if last is not None and last >= period_end:
            return

        portfolio = _get_portfolio_data(db, account)
        total_assets = float(portfolio.get("total_assets") or 0.0)
        if total_assets <= 0:
            return

        universe = [s for s in self.config.universe if s in prices and float(prices.get(s) or 0.0) > 0]
        if not universe:
            return

        target_total = total_assets * float(self.config.capital_usage)
        target_per_symbol = target_total / len(universe)

        # Build current quantities from DB for correctness (available_quantity etc.)
        positions: Dict[Tuple[str, str], Position] = {
            (p.market.upper(), p.symbol.upper()): p
            for p in db.query(Position).filter(Position.account_id == account.id).all()
        }

        try:
            for symbol in universe:
                market = _infer_market(symbol)
                if market == "US" and not _is_trading_open(symbol, market):
                    continue

                try:
                    price = float(prices[symbol])
                    pos = positions.get((market, symbol.upper()))

                    qty = float(pos.quantity) if pos is not None else 0.0
                    current_value = qty * price
                    delta_value = target_per_symbol - current_value

                    # If position is SHORT, neutralize first (baseline is long-only)
                    if pos is not None and (pos.side or "LONG").upper() == "SHORT" and float(pos.quantity) > 0:
                        # Close short by BUYing the full quantity
                        close_qty = float(pos.quantity)
                        if market == "US":
                            close_qty = float(int(close_qty))
                        if close_qty > 0:
                            self._place_market_order(db, account, symbol, market=market, side="BUY", quantity=close_qty)
                        continue

                    if abs(delta_value) < float(self.config.min_trade_usd):
                        continue

                    if delta_value > 0:
                        buy_qty = delta_value / price
                        if market == "US":
                            buy_qty = float(int(buy_qty))
                        if buy_qty * price >= float(self.config.min_trade_usd) and buy_qty > 0:
                            self._place_market_order(db, account, symbol, market=market, side="BUY", quantity=buy_qty)
                    else:
                        # Sell down, capped by available quantity
                        avail = float(pos.available_quantity) if pos is not None else 0.0
                        sell_qty = min(avail, (-delta_value) / price)
                        if market == "US":
                            sell_qty = float(int(sell_qty))
                        if sell_qty * price >= float(self.config.min_trade_usd) and sell_qty > 0:
                            self._place_market_order(db, account, symbol, market=market, side="SELL", quantity=sell_qty)
                except Exception:
                    logger.exception(
                        "BuyHold rebalance failed for account=%s symbol=%s market=%s",
                        account.id,
                        symbol,
                        market,
                    )
        finally:
            self._last_rebalance_end[account.id] = period_end

    def _place_market_order(self, db: Session, account: Account, symbol: str, market: str, side: str, quantity: float) -> None:
        name = symbol
        if market == "US":
            quantity = float(int(quantity))
            if quantity <= 0:
                return
        try:
            order = create_order(
                db=db,
                account=account,
                symbol=symbol,
                name=name,
                side=side,
                order_type="MARKET",
                price=None,
                quantity=float(int(quantity)) if market == "US" else float(round(quantity, 8)),
                leverage=1,
                market=market,
            )
            db.commit()
            db.refresh(order)
            executed = check_and_execute_order(db, order)
            if not executed:
                logger.info(
                    "BuyHold MARKET order not executed immediately: account=%s %s %s qty=%s",
                    account.id,
                    side,
                    symbol,
                    quantity,
                )
        except Exception:
            logger.exception(
                "Error placing MARKET order in BuyHold baseline: account=%s side=%s symbol=%s qty=%s market=%s",
                account.id,
                side,
                symbol,
                quantity,
                market,
            )
            try:
                db.rollback()
            except Exception:
                logger.exception("Error rolling back DB session after failed BuyHold MARKET order")


class GridBaseline:
    """Classic grid trading via LIMIT orders around current price (long-only)."""

    def __init__(self, config: Optional[GridConfig] = None):
        self.config = config or get_grid_config()

    def run_tick(self, db: Session, account: Account, prices: Dict[str, float]) -> None:
        universe = [s for s in self.config.universe if s in prices and float(prices.get(s) or 0.0) > 0]
        if not universe:
            return

        # 1) Clean up stale orders and ensure a bounded set of grid orders
        for symbol in universe:
            market = _infer_market(symbol)
            if market == "US" and not _is_trading_open(symbol, market):
                continue
            try:
                self._maintain_symbol_grid(db, account, symbol, market, float(prices[symbol]))
            except Exception as e:
                logger.warning(f"_maintain_symbol_grid failed for symbol={symbol} market={market}: {e}")

        # 2) Try executing any pending orders (in case order scheduler isn't running)
        try:
            process_all_pending_orders(db)
        except Exception as e:
            logger.warning(f"process_all_pending_orders failed: {e}")

    def _maintain_symbol_grid(self, db: Session, account: Account, symbol: str, market: str, current_price: float) -> None:
        cfg = self.config
        if current_price <= 0:
            return

        # Pending LIMIT orders for this symbol
        pending: List[Order] = (
            db.query(Order)
            .filter(
                Order.account_id == account.id,
                Order.market == market,
                Order.symbol == symbol,
                Order.status == "PENDING",
                Order.order_type == "LIMIT",
            )
            .order_by(Order.created_at.desc())
            .all()
        )

        # Cancel orders far away from current grid band
        max_band = cfg.levels * cfg.step_pct * cfg.cleanup_band_mult
        lower = current_price * (1.0 - max_band)
        upper = current_price * (1.0 + max_band)

        still_pending: List[Order] = []
        for o in pending:
            ref_price = float(o.price or 0.0)
            if ref_price <= 0:
                cancel_order(db, o, reason="grid: invalid price")
                continue
            if ref_price < lower or ref_price > upper:
                cancel_order(db, o, reason="grid: stale level")
                continue
            still_pending.append(o)

        # Cap max pending
        if len(still_pending) >= cfg.max_pending_per_symbol:
            return

        # Compute order size per level
        portfolio = _get_portfolio_data(db, account)
        total_assets = float(portfolio.get("total_assets") or 0.0)
        if total_assets <= 0:
            return

        budget = total_assets * cfg.capital_usage / max(1, len(cfg.universe))
        # Roughly split budget across buy levels; sells rely on inventory.
        per_buy_level_usd = max(cfg.min_order_usd, budget / max(1, cfg.levels))
        buy_qty = per_buy_level_usd / current_price
        if market == "US":
            buy_qty = float(int(buy_qty))

        # Ensure we have some inventory to sell: if no position, buy a small seed
        pos: Optional[Position] = (
            db.query(Position)
            .filter(
                Position.account_id == account.id,
                Position.market == market,
                Position.symbol == symbol,
            )
            .first()
        )
        if pos is not None and (pos.side or "LONG").upper() == "SHORT" and float(pos.quantity) > 0:
            close_qty = float(pos.quantity)
            if market == "US":
                close_qty = float(int(close_qty))
            if close_qty > 0:
                self._place_market_order(db, account, symbol, market=market, side="BUY", quantity=close_qty)
            pos = (
                db.query(Position)
                .filter(
                    Position.account_id == account.id,
                    Position.market == market,
                    Position.symbol == symbol,
                )
                .first()
            )

        if pos is None or float(pos.quantity) <= 0:
            seed_usd = max(cfg.min_order_usd, budget * 0.5)
            seed_qty = seed_usd / current_price
            if market == "US":
                seed_qty = float(int(seed_qty))
            self._place_market_order(db, account, symbol, market=market, side="BUY", quantity=seed_qty)
            # Refresh after seeding
            pos = (
                db.query(Position)
                .filter(
                    Position.account_id == account.id,
                    Position.market == market,
                    Position.symbol == symbol,
                )
                .first()
            )

        is_long_position = pos is not None and (pos.side or "LONG").upper() != "SHORT" and float(pos.quantity) > 0
        available_to_sell = float(pos.available_quantity) if is_long_position else 0.0
        per_sell_level_qty = max(0.0, available_to_sell / max(1, cfg.levels))
        if market == "US":
            per_sell_level_qty = float(int(per_sell_level_qty))

        price_precision = 2 if market == "US" else 8

        def _price_key(price: float) -> float:
            return round(float(price), price_precision)

        existing_keys = {
            (o.side.upper(), _price_key(float(o.price)))
            for o in still_pending
            if o.price is not None and float(o.price) > 0
        }

        created = 0
        for level in range(1, cfg.levels + 1):
            if len(still_pending) + created >= cfg.max_pending_per_symbol:
                break

            buy_price = current_price * (1.0 - cfg.step_pct * level)
            sell_price = current_price * (1.0 + cfg.step_pct * level)

            buy_key = ("BUY", _price_key(buy_price))
            if buy_key not in existing_keys:
                qty = float(int(buy_qty)) if market == "US" else float(round(buy_qty, 8))
                if qty * buy_price >= cfg.min_order_usd and qty > 0:
                    try:
                        self._place_limit_order(db, account, symbol, market=market, side="BUY", price=buy_price, quantity=qty)
                        created += 1
                    except Exception as e:
                        logger.debug(f"grid BUY order skipped: {e}")

            sell_key = ("SELL", _price_key(sell_price))
            if sell_key not in existing_keys:
                qty = float(int(per_sell_level_qty)) if market == "US" else float(round(per_sell_level_qty, 8))
                if qty * sell_price >= cfg.min_order_usd and qty > 0:
                    try:
                        self._place_limit_order(db, account, symbol, market=market, side="SELL", price=sell_price, quantity=qty)
                        created += 1
                    except Exception as e:
                        logger.debug(f"grid SELL order skipped: {e}")

    def _place_limit_order(self, db: Session, account: Account, symbol: str, market: str, side: str, price: float, quantity: float) -> None:
        name = symbol
        if market == "US":
            quantity = float(int(quantity))
            if quantity <= 0:
                return
            price = float(round(price, 2))
        try:
            order = create_order(
                db=db,
                account=account,
                symbol=symbol,
                name=name,
                side=side,
                order_type="LIMIT",
                price=float(price),
                quantity=float(int(quantity)) if market == "US" else float(quantity),
                leverage=1,
                market=market,
            )
            db.commit()
            db.refresh(order)
        except Exception:
            logger.exception(
                "Error placing LIMIT order in Grid baseline: account=%s side=%s symbol=%s qty=%s price=%s market=%s",
                account.id,
                side,
                symbol,
                quantity,
                price,
                market,
            )
            try:
                db.rollback()
            except Exception:
                logger.exception("Error rolling back DB session after failed Grid LIMIT order")

    def _place_market_order(self, db: Session, account: Account, symbol: str, market: str, side: str, quantity: float) -> None:
        name = symbol
        if market == "US":
            quantity = float(int(quantity))
            if quantity <= 0:
                return
        try:
            order = create_order(
                db=db,
                account=account,
                symbol=symbol,
                name=name,
                side=side,
                order_type="MARKET",
                price=None,
                quantity=float(int(quantity)) if market == "US" else float(round(quantity, 8)),
                leverage=1,
                market=market,
            )
            db.commit()
            db.refresh(order)
            check_and_execute_order(db, order)
        except Exception:
            logger.exception(
                "Error placing MARKET order in Grid baseline: account=%s side=%s symbol=%s qty=%s market=%s",
                account.id,
                side,
                symbol,
                quantity,
                market,
            )
            try:
                db.rollback()
            except Exception:
                logger.exception("Error rolling back DB session after failed Grid MARKET order")
