"""
Market K-line prefetch and persistence service
"""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session

from database.connection import SessionLocal
from database.models import MarketKline, Position
from repositories.kline_repo import KlineRepository
from services.alpaca_market_data import get_all_supported_symbols
from services.hyperliquid_market_data import get_kline_data_from_hyperliquid
from services.alpaca_market_data import get_kline_data_from_alpaca
from services.time_source import now_timestamp
from benchmark.infrastructure.market.symbols import CRYPTO_SYMBOLS as AI_TRADING_SYMBOLS

logger = logging.getLogger(__name__)

KLINE_REFRESH_INTERVAL_SECONDS = 60
KLINE_REFRESH_PERIOD = "1m"
KLINE_REFRESH_COUNT = 3
KLINE_MAX_STALE_SECONDS = 120


def _collect_symbols(db: Session) -> Dict[str, List[str]]:
    crypto_symbols = set(AI_TRADING_SYMBOLS)
    us_symbols = set(get_all_supported_symbols())

    positions = db.query(Position).filter(Position.quantity > 0).all()
    for pos in positions:
        if pos.market == "US":
            us_symbols.add(pos.symbol)
        else:
            crypto_symbols.add(pos.symbol)

    return {
        "CRYPTO": sorted(crypto_symbols),
        "US": sorted(us_symbols),
    }


def _latest_kline_timestamp(db: Session, symbol: str, market: str, period: str) -> int | None:
    row = (
        db.query(MarketKline)
        .filter(
            MarketKline.symbol == symbol,
            MarketKline.market == market,
            MarketKline.period == period,
        )
        .order_by(MarketKline.timestamp.desc())
        .first()
    )
    return row.timestamp if row else None


def _is_fresh(latest_ts: int | None) -> bool:
    if latest_ts is None:
        return False
    return (now_timestamp() - latest_ts) <= KLINE_REFRESH_INTERVAL_SECONDS


def _fetch_kline(symbol: str, market: str, period: str, count: int) -> List[dict]:
    if market == "US":
        return get_kline_data_from_alpaca(symbol, period=period, count=count)
    return get_kline_data_from_hyperliquid(symbol, period=period, count=count)


def refresh_market_klines() -> None:
    """Fetch latest K-lines and persist to DB for cache usage."""
    db = SessionLocal()
    try:
        symbol_map = _collect_symbols(db)
        repo = KlineRepository(db)

        for market, symbols in symbol_map.items():
            for symbol in symbols:
                try:
                    latest_ts = _latest_kline_timestamp(db, symbol, market, KLINE_REFRESH_PERIOD)
                    if _is_fresh(latest_ts):
                        continue

                    klines = _fetch_kline(symbol, market, KLINE_REFRESH_PERIOD, KLINE_REFRESH_COUNT)
                    if not klines:
                        logger.warning(f"No kline data for {symbol}.{market}")
                        continue

                    result = repo.save_kline_data(symbol, market, KLINE_REFRESH_PERIOD, klines)
                    logger.debug(
                        f"Saved kline data for {symbol}.{market}: {result['total']} rows"
                    )
                except Exception as e:
                    logger.error(f"Failed to refresh kline for {symbol}.{market}: {e}")
    finally:
        db.close()


def ensure_latest_kline(symbols: List[Tuple[str, str]]) -> None:
    """Ensure latest kline exists for given (symbol, market) pairs."""
    db = SessionLocal()
    try:
        repo = KlineRepository(db)
        for symbol, market in symbols:
            try:
                latest_ts = _latest_kline_timestamp(db, symbol, market, KLINE_REFRESH_PERIOD)
                if latest_ts and (now_timestamp() - latest_ts) <= KLINE_MAX_STALE_SECONDS:
                    continue
                klines = _fetch_kline(symbol, market, KLINE_REFRESH_PERIOD, KLINE_REFRESH_COUNT)
                if klines:
                    repo.save_kline_data(symbol, market, KLINE_REFRESH_PERIOD, klines)
            except Exception as e:
                logger.warning(f"ensure_latest_kline failed for {symbol}.{market}: {e}")
    finally:
        db.close()
