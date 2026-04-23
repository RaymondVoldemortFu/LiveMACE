"""
Asset Curve Calculator - New Algorithm
Draws curve by accounts, creates all-time list for every account: time, cash, positions.
Gets latest N close prices for all symbols, then fills curve with cash + sum(symbol price * position).
"""

from sqlalchemy.orm import Session, joinedload
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone
from decimal import Decimal
from bisect import bisect_right
import logging

from database.models import Trade, Account, AgentPeriodCheckpoint
from services.market_data import get_kline_data
from services.time_source import now_utc


def _to_epoch_seconds(value: Any) -> Optional[int]:
    """Best-effort normalize various timestamp formats to epoch seconds (int).

    Supports: int/float, numeric strings, epoch milliseconds, and ISO strings.
    Returns None if it cannot be parsed.
    """
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            ts = int(value)
        elif isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            # numeric string
            try:
                ts = int(float(s))
            except Exception:
                # ISO string
                iso = s.replace("Z", "+00:00")
                dt = datetime.fromisoformat(iso)
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = int(dt.timestamp())
        else:
            ts = int(value)

        # heuristic: epoch milliseconds
        if ts > 2_000_000_000_000:
            ts = ts // 1000
        return ts
    except Exception:
        return None


def _align_curve_timestamp(timeframe: str, ts_sec: int) -> int:
    # Most exchanges timestamp candles at the start of the interval.
    # Our checkpoints are labeled by period_end, so for 1h we align to hour-end.
    if timeframe == "1h":
        return ts_sec + 3600
    return ts_sec


def _lookup_price_with_last_close(price_map: Dict[int, float], ts: int) -> Optional[float]:
    """Lookup close price at ts; if missing, fallback to latest close before ts."""
    if not price_map:
        return None
    direct = price_map.get(ts)
    if direct is not None:
        return float(direct)

    keys = sorted(price_map.keys())
    idx = bisect_right(keys, ts) - 1
    if idx < 0:
        return None
    return float(price_map[keys[idx]])


def _list_active_accounts(db: Session) -> List[Account]:
    # Account.is_active is stored as a string in this project; keep this tolerant
    # so we don't accidentally return no accounts due to casing/data drift.
    active_values = ["true", "True", "TRUE", "1", "yes", "YES", "y", "Y"]
    accounts = db.query(Account).filter(Account.is_active.in_(active_values)).all()
    if accounts:
        return accounts
    # Fallback: return all accounts if none match active_values
    return db.query(Account).all()


def get_all_asset_curves_data_new(db: Session, timeframe: str = "1h", points: int = 20) -> List[Dict]:
    """
    New algorithm for asset curve calculation by accounts.
    
    Args:
        db: Database session
        timeframe: Time period for the curve, options: "5m", "1h", "1d"
        
    Returns:
        List of asset curve data points with timestamp, account info, and asset values
    """
    try:
        # Step 1: Get accounts (tolerant active flag)
        accounts = _list_active_accounts(db)
        if not accounts:
            return []

        points = max(1, int(points))

        # For 1h timeframe, use checkpoints as the source of truth.
        # This guarantees the curve deltas match the right-side hourly PnL.
        if timeframe == "1h":
            interval_seconds = 3600
            # IMPORTANT: multiple accounts share the same period_end; without DISTINCT
            # a small LIMIT will be consumed by duplicates and hide earlier hours.
            period_end_rows = (
                db.query(AgentPeriodCheckpoint.period_end)
                .filter(AgentPeriodCheckpoint.interval_seconds == interval_seconds)
                .distinct()
                .order_by(AgentPeriodCheckpoint.period_end.desc())
                .limit(points)
                .all()
            )
            period_ends = [r[0] for r in period_end_rows if r and r[0] is not None]
            period_ends = list(reversed(period_ends))

            if not period_ends:
                # No checkpoints yet; fall back to old behavior
                pass
            else:
                account_ids = [a.id for a in accounts]
                rows = (
                    db.query(AgentPeriodCheckpoint)
                    .filter(
                        AgentPeriodCheckpoint.interval_seconds == interval_seconds,
                        AgentPeriodCheckpoint.account_id.in_(account_ids),
                        AgentPeriodCheckpoint.period_end.in_(period_ends),
                    )
                    .order_by(AgentPeriodCheckpoint.period_end.asc())
                    .all()
                )

                by_account: Dict[int, Dict[datetime, float]] = {}
                for row in rows:
                    by_account.setdefault(row.account_id, {})[row.period_end] = float(row.equity_end)

                result: List[Dict] = []
                for account in accounts:
                    initial = float(account.initial_capital)
                    last_equity = initial
                    equity_map = by_account.get(account.id, {})
                    for pe in period_ends:
                        if pe in equity_map:
                            last_equity = equity_map[pe]

                        dt = pe
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        else:
                            dt = dt.astimezone(timezone.utc)
                        ts = int(dt.timestamp())
                        profit = last_equity - initial
                        profit_percentage = (profit / initial) * 100 if initial > 0 else 0.0

                        result.append({
                            "timestamp": ts,
                            "datetime_str": dt.isoformat(),
                            "account_id": account.id,
                            "user_id": account.user_id,
                            "username": account.name,
                            "total_assets": last_equity,
                            "initial_capital": initial,
                            "profit": profit,
                            "profit_percentage": profit_percentage,
                            "cash": 0.0,
                            "positions_value": 0.0,
                        })

                result.sort(key=lambda x: (x["timestamp"], x["account_id"]))
                return result
        
        logging.info(f"Found {len(accounts)} active accounts")
        
        # Step 2: Get all unique symbols from all account trades
        symbols_query = db.query(Trade.symbol, Trade.market).distinct().all()
        unique_symbols = set()
        for symbol, market in symbols_query:
            unique_symbols.add((symbol, market))
        
        if not unique_symbols:
            # No trades yet, return initial capital for all accounts at current time
            now = now_utc()
            return [{
                "timestamp": int(now.timestamp()),
                "datetime_str": now.isoformat(),
                "account_id": account.id,
                "user_id": account.user_id,
                "username": account.name,
                "total_assets": float(account.initial_capital),
                "initial_capital": float(account.initial_capital),
                "profit": 0.0,
                "profit_percentage": 0.0,
                "cash": float(account.initial_capital),
                "positions_value": 0.0,
            } for account in accounts]
        
        logging.info(f"Found {len(unique_symbols)} unique symbols: {unique_symbols}")
        
        # Step 3: Get latest N close prices for all symbols
        symbol_klines = {}
        for symbol, market in unique_symbols:
            try:
                klines = get_kline_data(symbol, market, timeframe, points)
                if klines:
                    symbol_klines[(symbol, market)] = klines
                    logging.info(f"Fetched {len(klines)} klines for {symbol}.{market}")
            except Exception as e:
                logging.warning(f"Failed to fetch klines for {symbol}.{market}: {e}")
        
        if not symbol_klines:
            # Fallback to current time if no market data available
            now = now_utc()
            return [{
                "timestamp": int(now.timestamp()),
                "datetime_str": now.isoformat(),
                "account_id": account.id,
                "user_id": account.user_id,
                "username": account.name,
                "total_assets": float(account.initial_capital),
                "initial_capital": float(account.initial_capital),
                "profit": 0.0,
                "profit_percentage": 0.0,
                "cash": float(account.initial_capital),
                "positions_value": 0.0,
            } for account in accounts]
        
        # Step 4: Choose a reference kline series for timestamps.
        # Dict insertion order depends on set iteration above, so we pick the series
        # with the most recent last timestamp to avoid showing stale x-axes.
        ref_klines = max(
            symbol_klines.values(),
            key=lambda ks: ((_to_epoch_seconds(ks[-1].get('timestamp')) or 0) if ks else 0),
        )

        timestamps: List[int] = []
        ts_to_datetime_str: Dict[int, str] = {}
        for k in ref_klines:
            ts_sec = _to_epoch_seconds(k.get("timestamp"))
            if ts_sec is None:
                continue
            aligned_ts = _align_curve_timestamp(timeframe, ts_sec)
            timestamps.append(aligned_ts)
            ts_to_datetime_str[aligned_ts] = datetime.fromtimestamp(aligned_ts, tz=timezone.utc).isoformat()

        if not timestamps:
            # Fallback if kline timestamps are missing/unparseable
            now = now_utc()
            timestamps = [int(now.timestamp())]
            ts_to_datetime_str[timestamps[0]] = now.isoformat()

        close_maps: Dict[Tuple[str, str], Dict[int, float]] = {}
        for key, klines in symbol_klines.items():
            m: Dict[int, float] = {}
            for kk in klines:
                ts = _to_epoch_seconds(kk.get('timestamp'))
                close = kk.get('close')
                if ts is None or close is None:
                    continue
                try:
                    aligned_ts = _align_curve_timestamp(timeframe, ts)
                    m[aligned_ts] = float(close)
                except Exception:
                    continue
            close_maps[key] = m
        
        logging.info(f"Processing {len(timestamps)} timestamps")
        
        # Step 5: Calculate asset curves for each account
        result = []
        
        for account in accounts:
            account_id = account.id
            logging.info(f"Processing account {account_id}: {account.name}")
            
            # Create all-time list for this account: time, cash, positions
            account_timeline = _create_account_timeline(db, account, timestamps, close_maps, ts_to_datetime_str)
            result.extend(account_timeline)
        
        # Sort result by timestamp and account_id for consistent ordering
        result.sort(key=lambda x: (x['timestamp'], x['account_id']))
        
        logging.info(f"Generated {len(result)} data points for asset curves")
        return result
        
    except Exception:
        logging.exception("Failed to calculate asset curves")
        return []


def _create_account_timeline(
    db: Session, 
    account: Account, 
    timestamps: List[int], 
    close_maps: Dict[Tuple[str, str], Dict[int, float]],
    ts_to_datetime_str: Dict[int, str],
) -> List[Dict]:
    """
    Create all-time list for an account: time, cash, positions.
    Calculate cash + sum(symbol price * position) for each timestamp.
    
    Args:
        db: Database session
        account: Account object
        timestamps: List of timestamps to calculate for
        symbol_klines: Dictionary of symbol klines data
        
    Returns:
        List of timeline data points for the account
    """
    account_id = account.id

    # Get all trades for this account, ordered by time
    # Load order in one query so we can read leverage for each trade replay step.
    trades = (
        db.query(Trade)
        .filter(Trade.account_id == account_id)
        .options(joinedload(Trade.order))
        .order_by(Trade.trade_time.asc())
        .all()
    )

    if not trades:
        # No trades, return initial capital at all timestamps
        return [{
            "timestamp": ts,
            "datetime_str": ts_to_datetime_str.get(ts) or datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "account_id": account.id,
            "user_id": account.user_id,
            "username": account.name,
            "total_assets": float(account.initial_capital),
            "initial_capital": float(account.initial_capital),
            "profit": 0.0,
            "profit_percentage": 0.0,
            "cash": float(account.initial_capital),
            "positions_value": 0.0,
        } for i, ts in enumerate(timestamps)]
    
    # Calculate holdings and cash at each timestamp
    timeline = []
    
    # Check if we should use actual account.current_cash for the last timestamp
    # This handles cases where cash was adjusted outside of trade history
    use_actual_cash_for_last = len(timestamps) > 0

    # Replay states from trade history to reconstruct historical cash/positions.
    # This is required for leverage correctness because leveraged open/close cash
    # changes are margin/PnL-based, not full notional-based.
    running_cash = float(account.initial_capital)
    trade_idx = 0
    position_state: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def _trade_time_utc(t: Trade) -> datetime:
        trade_time = t.trade_time
        if not getattr(trade_time, "tzinfo", None):
            return trade_time.replace(tzinfo=timezone.utc)
        return trade_time.astimezone(timezone.utc)

    def _trade_leverage(t: Trade) -> int:
        try:
            lev = int(getattr(getattr(t, "order", None), "leverage", 1) or 1)
            return lev if lev > 0 else 1
        except Exception:
            return 1

    def _apply_trade(t: Trade) -> None:
        nonlocal running_cash

        side = (t.side or "").upper()
        key = (t.symbol, t.market)

        price = Decimal(str(t.price or 0))
        qty = Decimal(str(t.quantity or 0))
        # Fee source compatibility:
        # - order_executor_leverage writes taker_fee
        # - order_matching may only write commission (taker_fee defaults to 0)
        # Prefer taker_fee only when it's non-zero, or when commission is zero.
        taker_fee_dec = Decimal(str(getattr(t, "taker_fee", 0) or 0))
        commission_dec = Decimal(str(getattr(t, "commission", 0) or 0))
        fee = taker_fee_dec if (taker_fee_dec != 0 or commission_dec == 0) else commission_dec
        interest = Decimal(str(t.interest_charged or 0))
        notional = price * qty

        lev_int = _trade_leverage(t)
        lev_dec = Decimal(str(lev_int))
        pos = position_state.get(key)

        def _add_or_update_position(target_side: str, open_leverage: int, open_qty: Decimal, open_price: Decimal) -> None:
            existing = position_state.get(key)
            if (
                existing
                and Decimal(str(existing.get("quantity", 0))) > 0
                and (existing.get("side") or "").upper() == target_side
            ):
                old_qty = Decimal(str(existing.get("quantity", 0)))
                old_avg = Decimal(str(existing.get("avg_cost", 0)))
                old_notional = old_qty * old_avg
                new_qty = old_qty + open_qty
                new_notional = old_notional + (open_price * open_qty)
                new_avg = (new_notional / new_qty) if new_qty > 0 else open_price

                old_lev = Decimal(str(existing.get("leverage", 1) or 1))
                new_lev_dec = (
                    (old_notional * old_lev + (open_price * open_qty) * Decimal(str(open_leverage))) / new_notional
                    if new_notional > 0
                    else Decimal(str(open_leverage))
                )
                new_lev = int(new_lev_dec) if int(new_lev_dec) > 0 else 1

                existing["quantity"] = float(new_qty)
                existing["avg_cost"] = float(new_avg)
                existing["leverage"] = new_lev
                existing["side"] = target_side
            else:
                position_state[key] = {
                    "quantity": float(open_qty),
                    "avg_cost": float(open_price),
                    "leverage": open_leverage if open_leverage > 0 else 1,
                    "side": target_side,
                }

        def _close_position(close_side: str, close_qty_req: Decimal, close_price: Decimal) -> bool:
            nonlocal running_cash
            existing = position_state.get(key)
            if not existing or Decimal(str(existing.get("quantity", 0))) <= 0:
                return False

            pos_qty = Decimal(str(existing.get("quantity", 0)))
            close_qty = close_qty_req if close_qty_req <= pos_qty else pos_qty
            pos_lev = Decimal(str(existing.get("leverage", 1) or 1))
            pos_side = (existing.get("side") or "LONG").upper()
            entry_price = Decimal(str(existing.get("avg_cost", 0)))
            entry_notional = entry_price * close_qty
            exit_notional = close_price * close_qty

            if pos_lev > 1:
                # Leveraged close: release margin + side-aware PnL - costs.
                if pos_side == "SHORT":
                    pnl = entry_notional - exit_notional
                else:
                    pnl = exit_notional - entry_notional
                margin_released = entry_notional / pos_lev
                running_cash += float(pnl + margin_released - fee - interest)
            else:
                # Spot conventions:
                # - close long with SELL => receive proceeds
                # - close short with BUY => pay buyback cost
                if pos_side == "SHORT" and close_side == "BUY":
                    running_cash -= float(exit_notional + fee + interest)
                else:
                    running_cash += float(exit_notional - fee - interest)

            remaining = pos_qty - close_qty
            if remaining <= 0:
                position_state.pop(key, None)
            else:
                existing["quantity"] = float(remaining)
            return True

        if side in ("LONG", "SHORT"):
            # Native leveraged open flow.
            initial_margin = (notional / lev_dec) if lev_dec > 0 else notional
            running_cash -= float(initial_margin + fee + interest)
            _add_or_update_position(side, lev_int, qty, price)
            return

        if side == "BUY":
            # BUY can be either close SHORT (US/legacy) or open/increase LONG.
            if pos and (pos.get("side") or "").upper() == "SHORT":
                _close_position("BUY", qty, price)
                return

            if lev_int > 1:
                initial_margin = (notional / lev_dec) if lev_dec > 0 else notional
                running_cash -= float(initial_margin + fee + interest)
            else:
                running_cash -= float(notional + fee + interest)
            _add_or_update_position("LONG", lev_int, qty, price)
            return

        if side == "SELL":
            # SELL can be close LONG (common) or open/increase SHORT (US/legacy).
            if pos and (pos.get("side") or "LONG").upper() != "SHORT":
                _close_position("SELL", qty, price)
                return

            if lev_int > 1:
                # If legacy flow sends leveraged short open as SELL, treat as margin open.
                initial_margin = (notional / lev_dec) if lev_dec > 0 else notional
                running_cash -= float(initial_margin + fee + interest)
            else:
                # Spot-style short open (US convention): receive sale proceeds.
                running_cash += float(notional - fee - interest)
            _add_or_update_position("SHORT", lev_int, qty, price)
            return

        # Fallback to legacy sign-based cash reconstruction for unknown/legacy trade side.
        trade_amount = float(notional + fee + interest)
        is_buy = side in ("BUY", "LONG")
        running_cash += (-trade_amount if is_buy else trade_amount)

    for i, ts in enumerate(timestamps):
        ts_datetime = datetime.fromtimestamp(ts, tz=timezone.utc)
        is_last_timestamp = (i == len(timestamps) - 1)

        # Apply all trades up to current timestamp exactly once.
        while trade_idx < len(trades):
            t = trades[trade_idx]
            if _trade_time_utc(t) > ts_datetime:
                break
            _apply_trade(t)
            trade_idx += 1

        
        # For the last timestamp, use actual current_cash to account for any realized P&L or adjustments
        # For historical points, reconstruct from initial capital + cash changes
        if is_last_timestamp and use_actual_cash_for_last:
            current_cash = float(account.current_cash)
        else:
            current_cash = running_cash
        
        # Calculate positions MARKET VALUE using prices at this timestamp
        # Market value = quantity * price (NOT * leverage!)
        # Leverage only affects margin requirement, not the position's equity value
        positions_value = 0.0
        
        # For the last timestamp, use actual Position table data to get accurate current positions
        if is_last_timestamp and use_actual_cash_for_last:
            from database.models import Position
            from services.market_data import get_last_price
            positions = db.query(Position).filter(Position.account_id == account.id).all()
            for pos in positions:
                if pos.quantity > 0:
                    try:
                        price = get_last_price(pos.symbol, pos.market)
                        if price is None or float(price) <= 0:
                            price = _lookup_price_with_last_close(
                                close_maps.get((pos.symbol, pos.market), {}),
                                ts,
                            )
                        if price and price > 0:
                            price_dec = Decimal(str(price))
                            quantity_dec = Decimal(str(pos.quantity))
                            avg_cost_dec = Decimal(str(pos.avg_cost))
                            leverage_dec = Decimal(str(pos.leverage)) if pos.leverage and pos.leverage > 0 else Decimal("1")

                            # Keep curve equity consistent with settlement/checkpoint logic:
                            # leverage > 1: entry margin + direction-aware unrealized PnL
                            # leverage == 1: quantity * current price
                            if leverage_dec > 1:
                                entry_margin = (quantity_dec * avg_cost_dec) / leverage_dec
                                side = (getattr(pos, "side", None) or "LONG").upper()
                                if side == "SHORT":
                                    unrealized_pnl = quantity_dec * (avg_cost_dec - price_dec)
                                else:
                                    unrealized_pnl = quantity_dec * (price_dec - avg_cost_dec)
                                position_equity = entry_margin + unrealized_pnl
                            else:
                                side = (getattr(pos, "side", None) or "LONG").upper()
                                signed_qty_dec = -quantity_dec if side == "SHORT" else quantity_dec
                                position_equity = signed_qty_dec * price_dec

                            positions_value += float(position_equity)
                    except Exception as e:
                        fallback_price = _lookup_price_with_last_close(
                            close_maps.get((pos.symbol, pos.market), {}),
                            ts,
                        )
                        if fallback_price is not None and fallback_price > 0:
                            price_dec = Decimal(str(fallback_price))
                            quantity_dec = Decimal(str(pos.quantity))
                            avg_cost_dec = Decimal(str(pos.avg_cost))
                            leverage_dec = Decimal(str(pos.leverage)) if pos.leverage and pos.leverage > 0 else Decimal("1")

                            if leverage_dec > 1:
                                entry_margin = (quantity_dec * avg_cost_dec) / leverage_dec
                                side = (getattr(pos, "side", None) or "LONG").upper()
                                if side == "SHORT":
                                    unrealized_pnl = quantity_dec * (avg_cost_dec - price_dec)
                                else:
                                    unrealized_pnl = quantity_dec * (price_dec - avg_cost_dec)
                                position_equity = entry_margin + unrealized_pnl
                            else:
                                side = (getattr(pos, "side", None) or "LONG").upper()
                                signed_qty_dec = -quantity_dec if side == "SHORT" else quantity_dec
                                position_equity = signed_qty_dec * price_dec

                            positions_value += float(position_equity)
                        else:
                            logging.warning(f"Could not get price for {pos.symbol}.{pos.market}: {e}")
        else:
            # For historical points, use replayed position states from trade history.
            # This avoids using current Position rows to infer history (which is lossy).
            for (symbol, market), pos in position_state.items():
                try:
                    if float(pos.get("quantity", 0)) <= 0:
                        continue

                    close = _lookup_price_with_last_close(
                        close_maps.get((symbol, market), {}),
                        ts,
                    )
                    if close is None:
                        continue

                    price_dec = Decimal(str(close))
                    quantity_dec = Decimal(str(pos.get("quantity", 0)))
                    avg_cost_dec = Decimal(str(pos.get("avg_cost", 0)))
                    lev_val = int(pos.get("leverage") or 1)
                    leverage_dec = Decimal(str(lev_val if lev_val > 0 else 1))
                    side = (pos.get("side") or "LONG").upper()

                    if leverage_dec > 1:
                        entry_margin = (quantity_dec * avg_cost_dec) / leverage_dec
                        if side == "SHORT":
                            unrealized_pnl = quantity_dec * (avg_cost_dec - price_dec)
                        else:
                            unrealized_pnl = quantity_dec * (price_dec - avg_cost_dec)
                        position_equity = entry_margin + unrealized_pnl
                    else:
                        signed_qty_dec = -quantity_dec if side == "SHORT" else quantity_dec
                        position_equity = signed_qty_dec * price_dec

                    positions_value += float(position_equity)
                except Exception:
                    continue
        
        total_assets = current_cash + positions_value
        # Calculate profit: total_assets - initial_capital
        profit = total_assets - float(account.initial_capital)
        # Calculate profit percentage
        profit_percentage = (profit / float(account.initial_capital)) * 100 if float(account.initial_capital) > 0 else 0
        
        timeline.append({
            "timestamp": ts,
            "datetime_str": ts_to_datetime_str.get(ts) or datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "account_id": account.id,
            "user_id": account.user_id,
            "username": account.name,
            "total_assets": total_assets,
            "initial_capital": float(account.initial_capital),
            "profit": profit,
            "profit_percentage": profit_percentage,
            "cash": current_cash,
            "positions_value": positions_value,
        })
    
    return timeline


def get_account_asset_curve(db: Session, account_id: int, timeframe: str = "1h", points: int = 20) -> List[Dict]:
    """
    Get asset curve data for a specific account.
    
    Args:
        db: Database session
        account_id: ID of the account to get curve for
        timeframe: Time period for the curve
        
    Returns:
        List of asset curve data points for the account
    """
    try:
        # Get the specific account
        account = db.query(Account).filter(
            Account.id == account_id,
            Account.is_active == "true"
        ).first()
        
        if not account:
            return []
        
        points = max(1, int(points))

        # For 1h timeframe, use checkpoints as the source of truth.
        if timeframe == "1h":
            interval_seconds = 3600
            rows = (
                db.query(AgentPeriodCheckpoint)
                .filter(
                    AgentPeriodCheckpoint.account_id == account_id,
                    AgentPeriodCheckpoint.interval_seconds == interval_seconds,
                )
                .order_by(AgentPeriodCheckpoint.period_end.desc())
                .limit(points)
                .all()
            )
            if not rows:
                return []
            rows = list(reversed(rows))
            initial = float(account.initial_capital)
            result: List[Dict] = []
            for row in rows:
                dt = row.period_end
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                ts = int(dt.timestamp())
                equity = float(row.equity_end)
                profit = equity - initial
                profit_percentage = (profit / initial) * 100 if initial > 0 else 0.0
                result.append({
                    "timestamp": ts,
                    "datetime_str": dt.isoformat(),
                    "account_id": account.id,
                    "user_id": account.user_id,
                    "username": account.name,
                    "total_assets": equity,
                    "initial_capital": initial,
                    "profit": profit,
                    "profit_percentage": profit_percentage,
                    "cash": 0.0,
                    "positions_value": 0.0,
                })
            return result

        # Get all unique symbols from this account's trades
        symbols_query = db.query(Trade.symbol, Trade.market).filter(
            Trade.account_id == account_id
        ).distinct().all()
        
        unique_symbols = set()
        for symbol, market in symbols_query:
            unique_symbols.add((symbol, market))
        
        if not unique_symbols:
            # No trades yet, return initial capital
            now = now_utc()
            return [{
                "timestamp": int(now.timestamp()),
                "datetime_str": now.isoformat(),
                "account_id": account.id,
                "user_id": account.user_id,
                "username": account.name,
                "total_assets": float(account.initial_capital),
                "initial_capital": float(account.initial_capital),
                "profit": 0.0,
                "profit_percentage": 0.0,
                "cash": float(account.initial_capital),
                "positions_value": 0.0,
            }]
        
        # Get latest N close prices for account's symbols
        symbol_klines = {}
        for symbol, market in unique_symbols:
            try:
                klines = get_kline_data(symbol, market, timeframe, points)
                if klines:
                    symbol_klines[(symbol, market)] = klines
            except Exception as e:
                logging.warning(f"Failed to fetch klines for {symbol}.{market}: {e}")
        
        if not symbol_klines:
            # Fallback to current time
            now = now_utc()
            return [{
                "timestamp": int(now.timestamp()),
                "datetime_str": now.isoformat(),
                "account_id": account.id,
                "user_id": account.user_id,
                "username": account.name,
                "total_assets": float(account.initial_capital),
                "initial_capital": float(account.initial_capital),
                "profit": 0.0,
                "profit_percentage": 0.0,
                "cash": float(account.initial_capital),
                "positions_value": 0.0,
            }]
        
        # Choose a reference kline series for timestamps (most recent last timestamp)
        ref_klines = max(
            symbol_klines.values(),
            key=lambda ks: ((_to_epoch_seconds(ks[-1].get('timestamp')) or 0) if ks else 0),
        )

        timestamps: List[int] = []
        ts_to_datetime_str: Dict[int, str] = {}
        for k in ref_klines:
            ts_sec = _to_epoch_seconds(k.get("timestamp"))
            if ts_sec is None:
                continue
            aligned_ts = _align_curve_timestamp(timeframe, ts_sec)
            timestamps.append(aligned_ts)
            ts_to_datetime_str[aligned_ts] = datetime.fromtimestamp(aligned_ts, tz=timezone.utc).isoformat()

        if not timestamps:
            now = now_utc()
            timestamps = [int(now.timestamp())]
            ts_to_datetime_str[timestamps[0]] = now.isoformat()

        close_maps: Dict[Tuple[str, str], Dict[int, float]] = {}
        for key, klines in symbol_klines.items():
            m: Dict[int, float] = {}
            for kk in klines:
                ts = _to_epoch_seconds(kk.get('timestamp'))
                close = kk.get('close')
                if ts is None or close is None:
                    continue
                try:
                    aligned_ts = _align_curve_timestamp(timeframe, ts)
                    m[aligned_ts] = float(close)
                except Exception:
                    continue
            close_maps[key] = m

        # Create timeline for this account
        timeline = _create_account_timeline(db, account, timestamps, close_maps, ts_to_datetime_str)
        
        return timeline
        
    except Exception as e:
        logging.error(f"Failed to get account asset curve for account {account_id}: {e}")
        return []