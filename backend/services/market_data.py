from typing import Dict, List, Any
import logging
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
)

logger = logging.getLogger(__name__)


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


def _resolve_market(symbol: str, market: str | None) -> str:
    market_norm = _normalize_market(market)
    if market_norm == "US" and _looks_like_crypto_symbol(symbol):
        return "CRYPTO"
    return market_norm


def get_last_price(symbol: str, market: str = "CRYPTO") -> float:
    key = f"{symbol}.{market}"
    market_norm = _resolve_market(symbol, market)
    
    # Check cache first
    from .price_cache import get_cached_price, cache_price
    cached_price = get_cached_price(symbol, market)
    if cached_price is not None:
        logger.debug(f"Using cached price for {key}: {cached_price}")
        return cached_price
    
    logger.info(f"Getting real-time price for {key} from API...")

    try:
        if market_norm == "US":
            source = "Alpaca"
            price = get_last_price_from_alpaca(symbol)
        else:
            source = "Hyperliquid"
            price = get_last_price_from_hyperliquid(symbol)
        if price and price > 0:
            logger.info(f"Got real-time price for {key} from {source}: {price}")
            # Cache the price
            cache_price(symbol, market, price)
            return price
        raise Exception(f"{source} returned invalid price: {price}")
    except Exception as hl_err:
        logger.error(f"Failed to get price from {source}: {hl_err}")
        raise Exception(f"Unable to get real-time price for {key}: {hl_err}")


def get_kline_data(symbol: str, market: str = "CRYPTO", period: str = "1d", count: int = 100, start_time: Any = None, end_time: Any = None) -> List[Dict[str, Any]]:
    key = f"{symbol}.{market}"
    market_norm = _resolve_market(symbol, market)

    try:
        if market_norm == "US":
            source = "Alpaca"
            data = get_kline_data_from_alpaca(symbol, period, count, start_time, end_time)
        else:
            source = "Hyperliquid"
            data = get_kline_data_from_hyperliquid(symbol, period, count, start_time, end_time)
        if data is not None:
            logger.info(f"Got K-line data for {key} from {source}, total {len(data)} items")
            return data
        raise Exception(f"{source} returned empty K-line data")
    except Exception as hl_err:
        logger.error(f"Failed to get K-line data from {source}: {hl_err}")
        raise Exception(f"Unable to get K-line data for {key}: {hl_err}")


def get_market_status(symbol: str, market: str = "CRYPTO") -> Dict[str, Any]:
    key = f"{symbol}.{market}"
    market_norm = _resolve_market(symbol, market)

    try:
        if market_norm == "US":
            source = "Alpaca"
            status = get_market_status_from_alpaca(symbol)
        else:
            source = "Hyperliquid"
            status = get_market_status_from_hyperliquid(symbol)
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
