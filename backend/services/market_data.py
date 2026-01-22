from typing import Dict, List, Any
import logging
from .hyperliquid_market_data import (
    get_last_price_from_hyperliquid,
    get_kline_data_from_hyperliquid,
    get_market_status_from_hyperliquid,
    get_all_symbols_from_hyperliquid,
    hyperliquid_client,
)
from .yfinance_market_data import (
    get_last_price_from_yfinance,
    get_kline_data_from_yfinance,
    get_market_status_from_yfinance,
    get_all_symbols_from_yfinance,
)
from config.settings import SUPPORTED_STOCKS

logger = logging.getLogger(__name__)


def is_stock(symbol: str) -> bool:
    return symbol in SUPPORTED_STOCKS


def get_last_price(symbol: str, market: str = "CRYPTO") -> float:
    market_key = "US" if is_stock(symbol) else market
    key = f"{symbol}.{market_key}"
    
    # Check cache first
    from .price_cache import get_cached_price, cache_price
    cached_price = get_cached_price(symbol, market_key)
    if cached_price is not None:
        logger.debug(f"Using cached price for {key}: {cached_price}")
        return cached_price
    
    logger.info(f"Getting real-time price for {key} from API...")

    if is_stock(symbol):
        try:
            price = get_last_price_from_yfinance(symbol)
            if price and price > 0:
                logger.info(f"Got price for {key} from YFinance: {price}")
                cache_price(symbol, market_key, price)
                return price
            # If stock is supported but no price found (e.g. market closed and no history?), return None or raise
            raise Exception(f"YFinance returned invalid price: {price}")
        except Exception as e:
             logger.error(f"Failed to get price from YFinance: {e}")
             raise Exception(f"Unable to get price for {key}: {e}")

    try:
        price = get_last_price_from_hyperliquid(symbol)
        if price and price > 0:
            logger.info(f"Got real-time price for {key} from Hyperliquid: {price}")
            # Cache the price
            cache_price(symbol, market_key, price)
            return price
        raise Exception(f"Hyperliquid returned invalid price: {price}")
    except Exception as hl_err:
        logger.error(f"Failed to get price from Hyperliquid: {hl_err}")
        raise Exception(f"Unable to get real-time price for {key}: {hl_err}")


def get_kline_data(symbol: str, market: str = "CRYPTO", period: str = "1d", count: int = 100, start_time: Any = None, end_time: Any = None) -> List[Dict[str, Any]]:
    market_key = "US" if is_stock(symbol) else market
    key = f"{symbol}.{market_key}"

    if is_stock(symbol):
        try:
            data = get_kline_data_from_yfinance(symbol, period, count, start_time, end_time)
            logger.info(f"Got K-line data for {key} from YFinance, total {len(data)} items")
            return data
        except Exception as e:
            logger.error(f"Failed to get K-line data from YFinance: {e}")
            raise Exception(f"Unable to get K-line data for {key}: {e}")

    try:
        data = get_kline_data_from_hyperliquid(symbol, period, count, start_time, end_time)
        if data is not None:
            logger.info(f"Got K-line data for {key} from Hyperliquid, total {len(data)} items")
            return data
        raise Exception("Hyperliquid returned empty K-line data")
    except Exception as hl_err:
        logger.error(f"Failed to get K-line data from Hyperliquid: {hl_err}")
        raise Exception(f"Unable to get K-line data for {key}: {hl_err}")


def get_market_status(symbol: str, market: str = "CRYPTO") -> Dict[str, Any]:
    market_key = "US" if is_stock(symbol) else market
    key = f"{symbol}.{market_key}"

    if is_stock(symbol):
        try:
            status = get_market_status_from_yfinance(symbol)
            return status
        except Exception as e:
             logger.error(f"Failed to get market status from YFinance: {e}")
             raise Exception(f"Unable to get market status for {key}: {e}")

    try:
        status = get_market_status_from_hyperliquid(symbol)
        logger.info(f"Retrieved market status for {key} from Hyperliquid: {status.get('market_status')}")
        return status
    except Exception as hl_err:
        logger.error(f"Failed to get market status: {hl_err}")
        raise Exception(f"Unable to get market status for {key}: {hl_err}")


def get_all_symbols() -> List[str]:
    """Get all available trading pairs"""
    try:
        symbols = get_all_symbols_from_hyperliquid()
        stock_symbols = get_all_symbols_from_yfinance()
        
        all_symbols = symbols + stock_symbols
        logger.info(f"Got {len(all_symbols)} trading pairs (Crypto + Stocks)")
        return all_symbols
    except Exception as hl_err:
        logger.error(f"Failed to get trading pairs list: {hl_err}")
        return ['BTC/USD', 'ETH/USD', 'SOL/USD']  # default trading pairs
