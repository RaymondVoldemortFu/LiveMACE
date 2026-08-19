from decimal import Decimal
from typing import Dict, List, Any
import logging
from threading import Lock
from datetime import datetime, timezone
from benchmark.contracts import Market
from benchmark.infrastructure.adapters.market import _FunctionMarketDataAdapter
from benchmark.infrastructure.cache import (
    LegacyPriceCacheAdapter,
    LegacySqlKlineCacheAdapter,
    LegacyToolCacheAdapter,
)
from benchmark.infrastructure.market import (
    DisplayMarketDataService,
    RoutedMarketDataPort,
    TradingMarketDataService,
)
from benchmark.infrastructure.market.symbols import (
    normalize_market,
    resolve_symbol_market,
)
from benchmark.providers import Freshness, KlineQuery, PriceResult
from .hyperliquid_market_data import (
    get_last_price_from_hyperliquid,
    get_kline_data_from_hyperliquid,
    get_market_status_from_hyperliquid,
    get_all_symbols_from_hyperliquid,
)
from .alpaca_market_data import (
    get_last_price_from_alpaca,
    get_last_close_price_from_alpaca,
    get_kline_data_from_alpaca,
    get_market_status_from_alpaca,
    get_all_supported_symbols,
)
from database.connection import SessionLocal
from database.models import MarketKline
from services.time_source import now_timestamp, now_utc
from repositories.kline_repo import KlineRepository
from config.tool_cache_config import ToolCacheConfig
from services.tool_cache import tool_cache
from services.price_cache import price_cache

logger = logging.getLogger(__name__)

KLINE_CACHE_PERIOD = "1m"
KLINE_CACHE_MAX_STALE_SECONDS = 120
US_MARKET_STATUS_TTL_SECONDS = 60
MARKET_COMPONENT_ID = "core.market.facade"
MARKET_COMPONENT_VERSION = "1.0.0"

_us_market_status_cache: Dict[str, Any] = {
    "status": None,
    "ts": 0,
}
_us_market_status_lock = Lock()


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
    return normalize_market(market).value


def _resolve_market(symbol: str, market: str | None) -> tuple[str, str]:
    resolved = resolve_symbol_market(symbol, market)
    return resolved.symbol, resolved.market.value


def _create_market_data_port() -> RoutedMarketDataPort:
    crypto = _FunctionMarketDataAdapter(
        provider_id="core.market.hyperliquid",
        price_loader=get_last_price_from_hyperliquid,
        kline_loader=get_kline_data_from_hyperliquid,
        status_loader=get_market_status_from_hyperliquid,
        supported_market=Market.CRYPTO,
    )
    us = _FunctionMarketDataAdapter(
        provider_id="core.market.alpaca",
        price_loader=get_last_price_from_alpaca,
        kline_loader=get_kline_data_from_alpaca,
        status_loader=get_market_status_from_alpaca,
        supported_market=Market.US,
    )
    return RoutedMarketDataPort({Market.CRYPTO: crypto, Market.US: us})


def _get_cached_latest_price_result(symbol: str, market: str) -> PriceResult | None:
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
        if row.close_price is None:
            return None
        return PriceResult(
            value=Decimal(str(row.close_price)),
            as_of=datetime.fromtimestamp(row.timestamp, tz=timezone.utc),
            source="core.market.sql-kline-cache",
            freshness=Freshness.STALE,
        )
    except Exception as cache_err:
        logger.warning("Latest price SQL cache unavailable for %s.%s, treating as miss: %s", symbol, market, cache_err)
        return None
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
    except Exception as cache_err:
        logger.warning("K-line SQL cache unavailable for %s.%s period=%s, treating as miss: %s", symbol, market, period, cache_err)
        return []
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


def _get_cached_us_market_status() -> Dict[str, Any]:
    """Get US market status with short TTL cache to avoid per-symbol clock calls."""
    now_ts = now_timestamp()
    with _us_market_status_lock:
        cached_status = _us_market_status_cache.get("status")
        cached_ts = int(_us_market_status_cache.get("ts") or 0)
        if cached_status is not None and (now_ts - cached_ts) <= US_MARKET_STATUS_TTL_SECONDS:
            return cached_status

    try:
        # Alpaca clock is market-wide (not symbol-specific); use a stable supported symbol.
        status = get_market_status_from_alpaca("AAPL")
    except Exception as err:
        with _us_market_status_lock:
            cached_status = _us_market_status_cache.get("status")
        if cached_status is not None:
            logger.warning(
                "Failed to refresh US market status, using stale cached status: %s",
                err,
            )
            return cached_status
        raise

    with _us_market_status_lock:
        _us_market_status_cache["status"] = status
        _us_market_status_cache["ts"] = now_timestamp()
    return status


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
    provider_id: str,
    provider_version: str,
) -> Dict[str, Any]:
    return {
        "component": MARKET_COMPONENT_ID,
        "component_version": MARKET_COMPONENT_VERSION,
        "provider": provider_id,
        "provider_version": provider_version,
        "symbol": symbol,
        "market": market,
        "period": str(period).strip().lower(),
        "count": int(count),
        "start_time": _normalize_time_for_kline_cache(start_time, period),
        "end_time": _normalize_time_for_kline_cache(end_time, period),
    }


def _display_price_fallback(symbol: str, market: Market) -> PriceResult | None:
    sql_result = _get_cached_latest_price_result(symbol, market.value)
    if sql_result is not None:
        return sql_result
    if market is not Market.US:
        return None
    try:
        status = _get_cached_us_market_status()
        if bool(status.get("is_trading", False)):
            return None
        close_price = get_last_close_price_from_alpaca(symbol)
        if close_price is None or close_price <= 0:
            return None
        return PriceResult(
            value=Decimal(str(close_price)),
            as_of=now_utc(),
            source="core.market.alpaca.close",
            freshness=Freshness.STALE,
        )
    except Exception as exc:
        logger.warning("Display price fallback failed for %s.%s: %s", symbol, market.value, exc)
        return None


def get_price_result(
    symbol: str,
    market: str = "CRYPTO",
    *,
    for_trading: bool = False,
    allow_stale: bool = True,
) -> PriceResult:
    resolved = resolve_symbol_market(symbol, market)
    port = _create_market_data_port()
    cache = LegacyPriceCacheAdapter(price_cache)
    if for_trading:
        return TradingMarketDataService(port, price_cache=cache).require_price(
            resolved.symbol,
            resolved.market,
        )
    return DisplayMarketDataService(
        port,
        price_cache=cache,
        fallback=_display_price_fallback,
    ).get_price(resolved.symbol, resolved.market, allow_stale=allow_stale)


def get_last_price(symbol: str, market: str = "CRYPTO") -> float:
    result = get_price_result(symbol, market, allow_stale=True)
    if result.value is None or result.value <= 0 or result.freshness is Freshness.UNAVAILABLE:
        detail = result.error or (
            f"source={result.source}, freshness={result.freshness.value}, "
            f"value={result.value}"
        )
        raise RuntimeError(f"Unable to get market price for {symbol}.{market}: {detail}")
    return float(result.value)


def get_trading_price(symbol: str, market: str = "CRYPTO") -> float:
    result = get_price_result(symbol, market, for_trading=True, allow_stale=False)
    if result.value is None or result.value <= 0 or result.freshness is not Freshness.FRESH:
        detail = result.error or (
            f"source={result.source}, freshness={result.freshness.value}, "
            f"value={result.value}"
        )
        raise RuntimeError(
            f"Unable to get fresh trading price for {symbol}.{market}: {detail}"
        )
    return float(result.value)


def get_kline_data(symbol: str, market: str = "CRYPTO", period: str = "1d", count: int = 100, start_time: Any = None, end_time: Any = None) -> List[Dict[str, Any]]:
    resolved = resolve_symbol_market(symbol, market)
    symbol_norm = resolved.symbol
    market_norm = resolved.market.value
    key = f"{symbol_norm}.{market_norm}"
    port = _create_market_data_port()
    provider_id, provider_version = port.provider_descriptor(resolved.market)
    query = KlineQuery(
        symbol=symbol_norm,
        market=resolved.market,
        period=str(period).strip().lower(),
        count=int(count),
        start_time=start_time,
        end_time=end_time,
    )
    redis_cache = LegacyToolCacheAdapter(tool_cache)
    sql_cache = LegacySqlKlineCacheAdapter(_get_cached_klines, _save_klines)
    round_id = tool_cache.get_current_round_id()
    cache_args = _build_kline_cache_args(
        symbol=symbol_norm,
        market=market_norm,
        period=period,
        count=count,
        start_time=start_time,
        end_time=end_time,
        provider_id=provider_id,
        provider_version=provider_version,
    )

    try:
        cached_tool_data = redis_cache.get("get_kline_data", cache_args, round_id=round_id)
        if isinstance(cached_tool_data, list):
            logger.debug(f"Using Redis tool cache for K-line data: {key} period={period} count={count}")
            return cached_tool_data

        cached = list(sql_cache.get(query))
        if cached:
            redis_cache.set(
                "get_kline_data",
                cache_args,
                cached,
                ttl_seconds=ToolCacheConfig.kline_ttl_seconds,
                round_id=round_id,
            )
            return cached

        # Use distributed lock to reduce duplicate upstream calls in concurrent multi-agent rounds.
        with redis_cache.acquire_lock("get_kline_data", cache_args, round_id=round_id) as lock_acquired:
            if lock_acquired:
                second_read = redis_cache.get(
                    "get_kline_data",
                    cache_args,
                    round_id=round_id,
                )
                if isinstance(second_read, list):
                    return second_read

            result = port.get_klines(query)
            data = [dict(row) for row in result.rows]
            if data:
                logger.info(f"Got K-line data for {key} from {result.source}, total {len(data)} items")
                try:
                    sql_cache.set(query, data)
                except Exception as save_err:
                    # K-line tool should prioritize returning successfully fetched market data.
                    # Persistence failure is observable via warning but must not fail the tool call.
                    logger.warning(
                        "K-line DB persistence failed for %s period=%s, returning provider data: %s",
                        key,
                        period,
                        save_err,
                    )
                redis_cache.set(
                    "get_kline_data",
                    cache_args,
                    data,
                    ttl_seconds=ToolCacheConfig.kline_ttl_seconds,
                    round_id=round_id,
                )
                return data
            raise RuntimeError(result.error or f"{result.source} returned empty K-line data")
    except Exception as hl_err:
        logger.error(f"Failed to get K-line data for {key}: {hl_err}")
        raise RuntimeError(f"Unable to get K-line data for {key}: {hl_err}") from hl_err


def get_market_status(symbol: str, market: str = "CRYPTO") -> Dict[str, Any]:
    resolved = resolve_symbol_market(symbol, market)
    key = f"{resolved.symbol}.{resolved.market.value}"
    try:
        result = _create_market_data_port().get_market_status(resolved.symbol, resolved.market)
        payload = dict(result.metadata)
        payload["is_trading"] = result.is_trading
        if result.reason is not None:
            payload["reason"] = result.reason
        payload.setdefault("market_status", "OPEN" if result.is_trading else "CLOSED")
        payload["source"] = result.source
        payload["as_of"] = result.as_of.isoformat() if result.as_of is not None else None
        if result.as_of is not None:
            payload.setdefault("timestamp", int(result.as_of.timestamp()))
            payload.setdefault("current_time", result.as_of.isoformat())
        logger.info(f"Retrieved market status for {key} from {result.source}: {payload.get('market_status')}")
        return payload
    except Exception as hl_err:
        logger.error(f"Failed to get market status: {hl_err}")
        raise RuntimeError(f"Unable to get market status for {key}: {hl_err}") from hl_err


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
    market_norm = normalize_market(market)
    if market_norm is Market.US:
        return get_all_supported_symbols()
    return get_all_symbols()
