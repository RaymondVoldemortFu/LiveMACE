#!/usr/bin/env python3
"""
Rebuild account_snapshots from hourly kline cache and historical trades.

This script is intentionally strict: missing prices, unknown trade sides, or
replay results that diverge from current account/position rows fail the run.
"""

from __future__ import annotations

import argparse
import sqlite3
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable


SNAPSHOT_ACCOUNT_IDS = (14, 15, 16, 17, 18)
HOURLY_SECONDS = 3600
MONEY_QUANT = Decimal("0.01")
STATE_TOLERANCE = Decimal("0.02")
QTY_TOLERANCE = Decimal("0.00000001")


@dataclass(frozen=True)
class AccountRow:
    id: int
    name: str
    initial_capital: Decimal
    current_cash: Decimal
    created_at: datetime


@dataclass(frozen=True)
class TradeRow:
    id: int
    order_id: int
    account_id: int
    symbol: str
    market: str
    side: str
    price: Decimal
    quantity: Decimal
    commission: Decimal
    taker_fee: Decimal
    interest_charged: Decimal
    trade_time: datetime
    leverage: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild account_snapshots from cached 1h klines and trades"
    )
    parser.add_argument(
        "--db-path",
        default="alpha_arena_final.sqlite",
        help="Path to the SQLite database",
    )
    parser.add_argument(
        "--account-id",
        type=int,
        action="append",
        dest="account_ids",
        help="Account id to rebuild. Can be passed more than once. Defaults to 14-18.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run validation and row generation, then roll back",
    )
    return parser.parse_args()


def to_decimal(value: object) -> Decimal:
    return Decimal(str(value if value is not None else "0"))


def quant_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def parse_db_datetime(value: str) -> datetime:
    if value is None:
        raise ValueError("datetime value is NULL")
    normalized = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def db_datetime_str(dt: datetime) -> str:
    utc_dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return utc_dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def aligned_kline_snapshot_ts(kline_timestamp: int) -> int:
    return int(kline_timestamp) + HOURLY_SECONDS


class Replayer:
    def __init__(self, initial_capital: Decimal) -> None:
        self.cash = initial_capital
        self.positions: dict[tuple[str, str], dict[str, Decimal | str | int]] = {}

    @staticmethod
    def trade_fee(trade: TradeRow) -> Decimal:
        if trade.taker_fee != 0 or trade.commission == 0:
            return trade.taker_fee
        return trade.commission

    def apply_trade(self, trade: TradeRow) -> None:
        side = trade.side.upper()
        if side not in {"LONG", "SHORT", "BUY", "SELL"}:
            raise ValueError(f"Unsupported trade side for trade id={trade.id}: {trade.side}")

        key = (trade.symbol, trade.market)
        price = trade.price
        quantity = trade.quantity
        fee = self.trade_fee(trade)
        interest = trade.interest_charged
        leverage = Decimal(str(trade.leverage if trade.leverage > 0 else 1))
        notional = price * quantity
        existing = self.positions.get(key)

        if side in {"LONG", "SHORT"}:
            self._open_position(key, side, quantity, price, int(leverage), notional, fee, interest)
            return

        if side == "BUY":
            if existing and str(existing["side"]).upper() == "SHORT":
                self._close_position(key, "BUY", quantity, price, fee, interest)
            else:
                self._open_position(key, "LONG", quantity, price, int(leverage), notional, fee, interest)
            return

        if side == "SELL":
            if existing and str(existing["side"]).upper() != "SHORT":
                self._close_position(key, "SELL", quantity, price, fee, interest)
            else:
                self._open_position(key, "SHORT", quantity, price, int(leverage), notional, fee, interest)
            return

    def _open_position(
        self,
        key: tuple[str, str],
        side: str,
        quantity: Decimal,
        price: Decimal,
        leverage: int,
        notional: Decimal,
        fee: Decimal,
        interest: Decimal,
    ) -> None:
        if leverage <= 0:
            raise ValueError(f"Invalid leverage for {key}: {leverage}")

        margin = notional / Decimal(str(leverage))
        self.cash -= margin + fee + interest

        existing = self.positions.get(key)
        if existing and Decimal(str(existing["quantity"])) > 0:
            existing_side = str(existing["side"]).upper()
            existing_leverage = int(existing["leverage"])
            if existing_side != side:
                raise ValueError(f"Cannot open {side} while holding {existing_side} for {key}")
            if existing_leverage != leverage:
                raise ValueError(
                    f"Cannot mix leverage for {key}: existing={existing_leverage}, new={leverage}"
                )

            old_qty = Decimal(str(existing["quantity"]))
            old_avg = Decimal(str(existing["avg_cost"]))
            new_qty = old_qty + quantity
            new_avg = ((old_qty * old_avg) + (quantity * price)) / new_qty
            existing["quantity"] = new_qty
            existing["avg_cost"] = new_avg
            return

        self.positions[key] = {
            "quantity": quantity,
            "avg_cost": price,
            "leverage": leverage,
            "side": side,
        }

    def _close_position(
        self,
        key: tuple[str, str],
        close_side: str,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal,
        interest: Decimal,
    ) -> None:
        existing = self.positions.get(key)
        if not existing or Decimal(str(existing["quantity"])) <= 0:
            raise ValueError(f"No open position to close for {key}")

        position_qty = Decimal(str(existing["quantity"]))
        if quantity - position_qty > QTY_TOLERANCE:
            raise ValueError(f"Close quantity exceeds position for {key}: {quantity} > {position_qty}")

        close_qty = min(quantity, position_qty)
        avg_cost = Decimal(str(existing["avg_cost"]))
        leverage = Decimal(str(existing["leverage"]))
        position_side = str(existing["side"]).upper()
        entry_notional = avg_cost * close_qty
        exit_notional = price * close_qty

        if leverage > 1:
            if close_side == "SELL" and position_side != "LONG":
                raise ValueError(f"SELL cannot close {position_side} for {key}")
            if close_side == "BUY" and position_side != "SHORT":
                raise ValueError(f"BUY cannot close {position_side} for {key}")

            pnl = (
                entry_notional - exit_notional
                if position_side == "SHORT"
                else exit_notional - entry_notional
            )
            margin_released = entry_notional / leverage
            self.cash += pnl + margin_released - fee - interest
        else:
            if position_side == "SHORT" and close_side == "BUY":
                self.cash -= exit_notional + fee + interest
            elif position_side == "LONG" and close_side == "SELL":
                self.cash += exit_notional - fee - interest
            else:
                raise ValueError(f"{close_side} cannot close {position_side} spot position for {key}")

        remaining = position_qty - close_qty
        if remaining <= QTY_TOLERANCE:
            self.positions.pop(key, None)
        else:
            existing["quantity"] = remaining

    def position_equity(self, key: tuple[str, str], close_price: Decimal) -> Decimal:
        position = self.positions[key]
        quantity = Decimal(str(position["quantity"]))
        avg_cost = Decimal(str(position["avg_cost"]))
        leverage = Decimal(str(position["leverage"]))
        side = str(position["side"]).upper()

        if leverage > 1:
            entry_margin = (quantity * avg_cost) / leverage
            pnl = quantity * (avg_cost - close_price) if side == "SHORT" else quantity * (close_price - avg_cost)
            return entry_margin + pnl

        signed_quantity = -quantity if side == "SHORT" else quantity
        return signed_quantity * close_price


def fetch_accounts(conn: sqlite3.Connection, account_ids: Iterable[int]) -> list[AccountRow]:
    placeholders = ",".join("?" for _ in account_ids)
    rows = conn.execute(
        f"""
        SELECT id, name, initial_capital, current_cash, created_at
        FROM accounts
        WHERE id IN ({placeholders})
        ORDER BY id
        """,
        tuple(account_ids),
    ).fetchall()
    accounts = [
        AccountRow(
            id=int(row["id"]),
            name=str(row["name"]),
            initial_capital=to_decimal(row["initial_capital"]),
            current_cash=to_decimal(row["current_cash"]),
            created_at=parse_db_datetime(row["created_at"]),
        )
        for row in rows
    ]
    requested = set(account_ids)
    found = {account.id for account in accounts}
    missing = sorted(requested - found)
    if missing:
        raise ValueError(f"Account ids not found: {missing}")
    return accounts


def fetch_trades(conn: sqlite3.Connection, account_id: int) -> list[TradeRow]:
    rows = conn.execute(
        """
        SELECT
            t.id,
            t.order_id,
            t.account_id,
            t.symbol,
            t.market,
            t.side,
            t.price,
            t.quantity,
            t.commission,
            t.taker_fee,
            t.interest_charged,
            t.trade_time,
            COALESCE(o.leverage, 1) AS leverage
        FROM trades t
        LEFT JOIN orders o ON o.id = t.order_id
        WHERE t.account_id = ?
        ORDER BY t.trade_time ASC, t.id ASC
        """,
        (account_id,),
    ).fetchall()
    if not rows:
        raise ValueError(f"No trades found for account_id={account_id}")

    return [
        TradeRow(
            id=int(row["id"]),
            order_id=int(row["order_id"]),
            account_id=int(row["account_id"]),
            symbol=str(row["symbol"]),
            market=str(row["market"]),
            side=str(row["side"]),
            price=to_decimal(row["price"]),
            quantity=to_decimal(row["quantity"]),
            commission=to_decimal(row["commission"]),
            taker_fee=to_decimal(row["taker_fee"]),
            interest_charged=to_decimal(row["interest_charged"]),
            trade_time=parse_db_datetime(row["trade_time"]),
            leverage=int(row["leverage"] or 1),
        )
        for row in rows
    ]


def fetch_close_maps(
    conn: sqlite3.Connection,
    symbols: set[tuple[str, str]],
) -> tuple[dict[tuple[str, str], dict[int, Decimal]], list[int]]:
    close_maps: dict[tuple[str, str], dict[int, Decimal]] = {}
    all_timestamps: set[int] = set()

    for symbol, market in sorted(symbols):
        rows = conn.execute(
            """
            SELECT timestamp, close_price
            FROM market_klines
            WHERE symbol = ?
              AND market = ?
              AND period = '1h'
              AND close_price IS NOT NULL
            ORDER BY timestamp ASC
            """,
            (symbol, market),
        ).fetchall()
        if not rows:
            raise ValueError(f"No cached 1h klines found for {symbol}.{market}")

        price_map: dict[int, Decimal] = {}
        for row in rows:
            snapshot_ts = aligned_kline_snapshot_ts(int(row["timestamp"]))
            close = to_decimal(row["close_price"])
            price_map[snapshot_ts] = close
            all_timestamps.add(snapshot_ts)

        close_maps[(symbol, market)] = price_map

    return close_maps, sorted(all_timestamps)


def lookup_cached_close(
    close_maps: dict[tuple[str, str], dict[int, Decimal]],
    key: tuple[str, str],
    snapshot_ts: int,
) -> Decimal:
    price_map = close_maps.get(key)
    if not price_map:
        raise ValueError(f"No price map loaded for {key}")
    timestamps = sorted(price_map.keys())
    idx = bisect_right(timestamps, snapshot_ts) - 1
    if idx < 0:
        raise ValueError(f"No cached 1h close at or before ts={snapshot_ts} for {key}")
    return price_map[timestamps[idx]]


def build_snapshot_rows(
    account: AccountRow,
    trades: list[TradeRow],
    close_maps: dict[tuple[str, str], dict[int, Decimal]],
    candidate_timestamps: list[int],
) -> tuple[list[tuple[int, str, Decimal, Decimal, Decimal]], Replayer]:
    account_created_ts = int(account.created_at.timestamp())
    last_trade_ts = int(max(trade.trade_time for trade in trades).timestamp())
    end_ts = max(max(candidate_timestamps), last_trade_ts)
    timeline = sorted(
        {
            account_created_ts,
            last_trade_ts,
            *(
                ts
                for ts in candidate_timestamps
                if account_created_ts <= ts <= end_ts
            ),
        }
    )
    if len(timeline) < 2:
        raise ValueError(f"Insufficient timeline points for account_id={account.id}")

    replayer = Replayer(account.initial_capital)
    trade_idx = 0
    rows: list[tuple[int, str, Decimal, Decimal, Decimal]] = []

    for snapshot_ts in timeline:
        snapshot_dt = datetime.fromtimestamp(snapshot_ts, tz=timezone.utc)
        while trade_idx < len(trades) and trades[trade_idx].trade_time <= snapshot_dt:
            replayer.apply_trade(trades[trade_idx])
            trade_idx += 1

        positions_value = Decimal("0")
        for key in sorted(replayer.positions.keys()):
            if Decimal(str(replayer.positions[key]["quantity"])) <= 0:
                continue
            close = lookup_cached_close(close_maps, key, snapshot_ts)
            positions_value += replayer.position_equity(key, close)

        cash = quant_money(replayer.cash)
        positions_value = quant_money(positions_value)
        total_equity = quant_money(cash + positions_value)
        rows.append((snapshot_ts, db_datetime_str(snapshot_dt), total_equity, cash, positions_value))

    while trade_idx < len(trades):
        replayer.apply_trade(trades[trade_idx])
        trade_idx += 1

    return rows, replayer


def validate_current_state(
    conn: sqlite3.Connection,
    account: AccountRow,
    replayer: Replayer,
) -> None:
    cash_delta = abs(quant_money(replayer.cash) - quant_money(account.current_cash))
    if cash_delta > STATE_TOLERANCE:
        raise ValueError(
            f"Cash replay mismatch for account_id={account.id}: "
            f"replayed={quant_money(replayer.cash)}, current={quant_money(account.current_cash)}"
        )

    rows = conn.execute(
        """
        SELECT symbol, market, quantity, avg_cost, leverage, side
        FROM positions
        WHERE account_id = ?
          AND quantity > 0
        ORDER BY symbol, market
        """,
        (account.id,),
    ).fetchall()
    db_positions = {(str(row["symbol"]), str(row["market"])): row for row in rows}

    replay_positions = {
        key: value
        for key, value in replayer.positions.items()
        if Decimal(str(value["quantity"])) > QTY_TOLERANCE
    }

    if set(db_positions.keys()) != set(replay_positions.keys()):
        raise ValueError(
            f"Position keys mismatch for account_id={account.id}: "
            f"replayed={sorted(replay_positions.keys())}, db={sorted(db_positions.keys())}"
        )

    for key, db_row in db_positions.items():
        replayed = replay_positions[key]
        quantity_delta = abs(Decimal(str(replayed["quantity"])) - to_decimal(db_row["quantity"]))
        avg_cost_delta = abs(Decimal(str(replayed["avg_cost"])) - to_decimal(db_row["avg_cost"]))
        replay_side = str(replayed["side"]).upper()
        db_side = str(db_row["side"] or "LONG").upper()
        replay_leverage = int(replayed["leverage"])
        db_leverage = int(db_row["leverage"] or 1)

        if quantity_delta > QTY_TOLERANCE:
            raise ValueError(f"Quantity mismatch for account_id={account.id} {key}")
        if avg_cost_delta > Decimal("0.000001"):
            raise ValueError(f"Average cost mismatch for account_id={account.id} {key}")
        if replay_leverage != db_leverage:
            raise ValueError(f"Leverage mismatch for account_id={account.id} {key}")
        if replay_side != db_side:
            raise ValueError(f"Side mismatch for account_id={account.id} {key}")


def rebuild_account(conn: sqlite3.Connection, account: AccountRow) -> tuple[int, int]:
    trades = fetch_trades(conn, account.id)
    symbols = {(trade.symbol, trade.market) for trade in trades}
    close_maps, candidate_timestamps = fetch_close_maps(conn, symbols)
    snapshot_rows, final_replayer = build_snapshot_rows(
        account,
        trades,
        close_maps,
        candidate_timestamps,
    )
    validate_current_state(conn, account, final_replayer)

    before_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM account_snapshots WHERE account_id = ?",
            (account.id,),
        ).fetchone()[0]
    )
    conn.execute("DELETE FROM account_snapshots WHERE account_id = ?", (account.id,))
    conn.executemany(
        """
        INSERT INTO account_snapshots (
            account_id,
            ts,
            total_equity,
            cash,
            positions_value
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                account.id,
                ts_str,
                str(total_equity),
                str(cash),
                str(positions_value),
            )
            for _, ts_str, total_equity, cash, positions_value in snapshot_rows
        ],
    )
    return before_count, len(snapshot_rows)


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path).expanduser()
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"SQLite DB not found: {db_path}")

    account_ids = tuple(args.account_ids or SNAPSHOT_ACCOUNT_IDS)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        accounts = fetch_accounts(conn, account_ids)
        total_inserted = 0
        for account in accounts:
            before_count, inserted_count = rebuild_account(conn, account)
            total_inserted += inserted_count
            print(
                f"account_id={account.id} name={account.name} "
                f"before={before_count} rebuilt={inserted_count}"
            )

        if args.dry_run:
            conn.rollback()
            print(f"DRY-RUN complete: generated {total_inserted} rows, rolled back")
        else:
            conn.commit()
            print(f"DONE: rebuilt {total_inserted} account_snapshots rows")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
