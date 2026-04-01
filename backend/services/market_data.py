from typing import Dict, List, Any
import logging
from datetime import datetime, timezone
from .hyperliquid_market_data import (
    get_last_price_from_hyperliquid,
    get_kline_data_from_hyperliquid,
    get_market_status_from_hyperliquid,
    get_all_symbols_from_hyperliquid,
    hyperliquid_client,
)
from .alpaca_market_data import (
    get_last_price_from_alpaca,
    get_kline_data_from_alpaca,
    get_market_status_from_alpaca,
    get_all_supported_symbols,
    SUPPORTED_STOCKS,
)
from database.connection import SessionLocal
from database.models import MarketKline
from services.time_source import now_timestamp
from repositories.kline_repo import KlineRepository
from config.tool_cache_config import ToolCacheConfig
from services.tool_cache import tool_cache

logger = logging.getLogger(__name__)
US_STOCK_SYMBOLS = {str(symbol).upper() for symbol in SUPPORTED_STOCKS}

KLINE_CACHE_PERIOD = "1m"
KLINE_CACHE_MAX_STALE_SECONDS = 120


def _period_to_seconds(period: str) -> int | None:
    if not period:
        return None
    p = str(period).strip().lower()
    try:
        if p.endswith("m"):
            return int(p[:-1]) * 60
        if p.endswith("h"):
            return int(p[:-1]) * 60 * 60
        if p.endswith("d"):
            return int(p[:-1]) * 24 * 60 * 60
    except ValueError:
        return None
    return None


def _normalize_market(market: str | None) -> str:
    if not market:
        return "CRYPTO"
    market_upper = str(market).upper()
    if market_upper in ("US", "STOCK", "STOCKS"):
        return "US"
    if market_upper in ("CRYPTO", "HYPERLIQUID"):
        return "CRYPTO"
    return market_upper


def _looks_like_crypto_symbol(symbol: str) -> bool:
    return "/" in symbol or ":" in symbol


def _normalize_symbol(symbol: str) -> str:
    symbol_norm = str(symbol or "").strip().upper()
    if "." in symbol_norm:
        raise ValueError(
            f"Invalid symbol format '{symbol_norm}': do not append market suffix in symbol, "
            "pass market via the 'market' parameter instead."
        )
    return symbol_norm


def _validate_symbol_market(symbol: str, market: str) -> None:
    if market == "US":
        if _looks_like_crypto_symbol(symbol):
            raise ValueError(f"Invalid US symbol '{symbol}': crypto pair format is not allowed for US market")
        if symbol not in US_STOCK_SYMBOLS:
            raise ValueError(f"Unsupported US stock symbol: {symbol}")
    elif market == "CRYPTO":
        if symbol in US_STOCK_SYMBOLS:
            raise ValueError(
                f"Invalid market for symbol '{symbol}': symbol belongs to US stock list, use market='US'"
            )


def _resolve_market(symbol: str, market: str | None) -> tuple[str, str]:
    symbol_norm = _normalize_symbol(symbol)
    market_norm = _normalize_market(market)
    _validate_symbol_market(symbol_norm, market_norm)
    return symbol_norm, market_norm


def _get_cached_latest_price(symbol: str, market: str) -> float | None:
    if not symbol:
        return None

    db = SessionLocal()
    try:
        row = (
            db.query(MarketKline)
            .filter(
                MarketKline.symbol == symbol,
                MarketKline.market == market,
                MarketKline.period == KLINE_CACHE_PERIOD,
            )
            .order_by(MarketKline.timestamp.desc())
            .first()
        )
        if not row:
            return None
        if (now_timestamp() - row.timestamp) > KLINE_CACHE_MAX_STALE_SECONDS:
            return None
        return float(row.close_price) if row.close_price is not None else None
    finally:
        db.close()


def _get_cached_klines(symbol: str, market: str, period: str, count: int) -> List[Dict[str, Any]]:
    if not symbol:
        return []

    db = SessionLocal()
    try:
        rows = (
            db.query(MarketKline)
            .filter(
                MarketKline.symbol == symbol,
                MarketKline.market == market,
                MarketKline.period == period,
            )
            .order_by(MarketKline.timestamp.desc())
            .limit(count)
            .all()
        )
        if not rows:
            return []

        rows_sorted = sorted(rows, key=lambda r: r.timestamp)
        latest_ts = rows_sorted[-1].timestamp
        period_seconds = _period_to_seconds(period)
        # Period-aware stale threshold:
        # - For small periods, require fairly fresh data
        # - For large periods (e.g., 1d), allow data within a candle duration
        stale_threshold = max(
            KLINE_CACHE_MAX_STALE_SECONDS,
            int(period_seconds) if period_seconds else KLINE_CACHE_MAX_STALE_SECONDS,
        )
        if (now_timestamp() - latest_ts) > stale_threshold:
            return []

        return [
            {
                "timestamp": r.timestamp,
                "datetime_str": r.datetime_str,
                "open": float(r.open_price) if r.open_price is not None else None,
                "high": float(r.high_price) if r.high_price is not None else None,
                "low": float(r.low_price) if r.low_price is not None else None,
                "close": float(r.close_price) if r.close_price is not None else None,
                "volume": float(r.volume) if r.volume is not None else None,
                "amount": float(r.amount) if r.amount is not None else None,
                "change": float(r.change) if r.change is not None else None,
                "percent": float(r.percent) if r.percent is not None else None,
            }
            for r in rows_sorted
        ]
    finally:
        db.close()


def _save_klines(symbol: str, market: str, period: str, klines: List[Dict[str, Any]]) -> None:
    if not klines:
        return
    db = SessionLocal()
    try:
        repo = KlineRepository(db)
        repo.save_kline_data(symbol, market, period, klines)
    finally:
        db.close()


def _iso_string_to_utc_epoch_ms(raw: str) -> int:
    """ISO 8601 → UTC 瞬时点毫秒。naive 按 UTC 解释，与行情层一致，避免依赖服务器本地时区。"""
    ts = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp() * 1000)


def _normalize_time_for_kline_cache(value: Any, period: str) -> Any:
    """
    Normalize time-like values into period-aligned millisecond buckets so
    semantically equivalent requests can share Redis tool-cache keys.
    """
    if value is None:
        return None

    ts_ms: int | None = None
    if isinstance(value, (int, float)):
        ts_ms = int(value)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            ts_ms = int(float(raw))
        except ValueError:
            try:
                ts_ms = _iso_string_to_utc_epoch_ms(raw)
            except ValueError:
                return value
    else:
        return value

    period_seconds = _period_to_seconds(period)
    if not period_seconds or period_seconds <= 0:
        return ts_ms

    bucket_ms = period_seconds * 1000
    return (ts_ms // bucket_ms) * bucket_ms


def _build_kline_cache_args(
    symbol: str,
    market: str,
    period: str,
    count: int,
    start_time: Any,
    end_time: Any,
) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "market": market,
        "period": period,
        "count": count,
        "start_time": _normalize_time_for_kline_cache(start_time, period),
        "end_time": _normalize_time_for_kline_cache(end_time, period),
    }


def get_last_price(symbol: str, market: str = "CRYPTO") -> float:
    symbol_norm, market_norm = _resolve_market(symbol, market)
    key = f"{symbol_norm}.{market_norm}"
    
    # Check cache first
    from .price_cache import get_cached_price, cache_price
    cached_price = get_cached_price(symbol_norm, market_norm)
    if cached_price is not None:
        logger.debug(f"Using cached price for {key}: {cached_price}")
        return cached_price

    cached_db_price = _get_cached_latest_price(symbol_norm, market_norm)
    if cached_db_price is not None:
        cache_price(symbol_norm, market_norm, cached_db_price)
        return cached_db_price
    
    logger.info(f"Getting real-time price for {key} from API...")

    try:
        if market_norm == "US":
            source = "Alpaca"
            price = get_last_price_from_alpaca(symbol_norm)
        else:
            source = "Hyperliquid"
            price = get_last_price_from_hyperliquid(symbol_norm)
        if price and price > 0:
            logger.info(f"Got real-time price for {key} from {source}: {price}")
            # Cache the price
            cache_price(symbol_norm, market_norm, price)
            return price
        raise Exception(f"{source} returned invalid price: {price}")
    except Exception as hl_err:
        logger.error(f"Failed to get price from {source}: {hl_err}")
        raise Exception(f"Unable to get real-time price for {key}: {hl_err}")


def get_kline_data(symbol: str, market: str = "CRYPTO", period: str = "1d", count: int = 100, start_time: Any = None, end_time: Any = None) -> List[Dict[str, Any]]:
    symbol_norm, market_norm = _resolve_market(symbol, market)
    key = f"{symbol_norm}.{market_norm}"
    round_id = tool_cache.get_current_round_id()
    cache_args = _build_kline_cache_args(
        symbol=symbol_norm,
        market=market_norm,
        period=period,
        count=count,
        start_time=start_time,
        end_time=end_time,
    )

    try:
        cached_tool_data = tool_cache.get_json("get_kline_data", cache_args, round_id=round_id)
        if isinstance(cached_tool_data, list):
            logger.debug(f"Using Redis tool cache for K-line data: {key} period={period} count={count}")
            return cached_tool_data

        if start_time is None and end_time is None:
            cached = _get_cached_klines(symbol_norm, market_norm, period, count)
            if cached:
                tool_cache.set_json(
                    "get_kline_data",
                    cache_args,
                    cached,
                    ttl_seconds=ToolCacheConfig.kline_ttl_seconds,
                    round_id=round_id,
                )
                return cached

        def _load_from_provider() -> tuple[str, List[Dict[str, Any]]]:
            if market_norm == "US":
                selected_source = "Alpaca"
                selected_data = get_kline_data_from_alpaca(symbol_norm, period, count, start_time, end_time)
            else:
                selected_source = "Hyperliquid"
                selected_data = get_kline_data_from_hyperliquid(symbol_norm, period, count, start_time, end_time)
            return selected_source, selected_data

        # Use distributed lock to reduce duplicate upstream calls in concurrent multi-agent rounds.
        with tool_cache.acquire_lock("get_kline_data", cache_args, round_id=round_id) as lock_acquired:
            if lock_acquired:
                second_read = tool_cache.get_json(
                    "get_kline_data",
                    cache_args,
                    round_id=round_id,
                    suppress_miss_log=True,
                )
                if isinstance(second_read, list):
                    return second_read

            source, data = _load_from_provider()
            if data is not None:
                logger.info(f"Got K-line data for {key} from {source}, total {len(data)} items")
                _save_klines(symbol_norm, market_norm, period, data)
                tool_cache.set_json(
                    "get_kline_data",
                    cache_args,
                    data,
                    ttl_seconds=ToolCacheConfig.kline_ttl_seconds,
                    round_id=round_id,
                )
                return data
            raise Exception(f"{source} returned empty K-line data")
    except Exception as hl_err:
        logger.error(f"Failed to get K-line data for {key}: {hl_err}")
        raise Exception(f"Unable to get K-line data for {key}: {hl_err}")


def get_market_status(symbol: str, market: str = "CRYPTO") -> Dict[str, Any]:
    symbol_norm, market_norm = _resolve_market(symbol, market)
    key = f"{symbol_norm}.{market_norm}"

    try:
        if market_norm == "US":
            source = "Alpaca"
            status = get_market_status_from_alpaca(symbol_norm)
        else:
            source = "Hyperliquid"
            status = get_market_status_from_hyperliquid(symbol_norm)
        logger.info(f"Retrieved market status for {key} from {source}: {status.get('market_status')}")
        return status
    except Exception as hl_err:
        logger.error(f"Failed to get market status: {hl_err}")
        raise Exception(f"Unable to get market status for {key}: {hl_err}")


def get_all_symbols() -> List[str]:
    """Get all available trading pairs"""
    try:
        symbols = get_all_symbols_from_hyperliquid()
        logger.info(f"Got {len(symbols)} trading pairs from Hyperliquid")
        return symbols
    except Exception as hl_err:
        logger.error(f"Failed to get trading pairs list: {hl_err}")
        return ['BTC/USD', 'ETH/USD', 'SOL/USD']  # default trading pairs


def get_all_symbols_by_market(market: str = "CRYPTO") -> List[str]:
    market_norm = _normalize_market(market)
    if market_norm == "US":
        return get_all_supported_symbols()
    return get_all_symbols()
