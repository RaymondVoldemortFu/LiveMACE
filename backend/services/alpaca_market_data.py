"""
Alpaca US stock market data service
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Dict, List, Any, Optional

import dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, StockBarsRequest
from alpaca.data.enums import DataFeed
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient

from benchmark.infrastructure.market.symbols import US_SYMBOLS
from config.agent_config import AgentConfig
from services.time_source import now_utc, delta_t_minutes
from config.market_data_config import ALPACA_US_FEED_ENABLED, ALPACA_US_FEED

dotenv.load_dotenv()

logger = logging.getLogger(__name__)

SUPPORTED_STOCKS = list(US_SYMBOLS)


class RateLimiter:
    def __init__(self, max_calls: int, window_seconds: int = 60):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._lock = Lock()
        self._calls: List[float] = []

    def wait_for_slot(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._calls = [t for t in self._calls if now - t < self.window_seconds]
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                wait_seconds = self.window_seconds - (now - self._calls[0])
            if wait_seconds > 0:
                time.sleep(min(wait_seconds, 1.0))


@dataclass(frozen=True)
class AlpacaConfig:
    api_key: str
    api_secret: str


def _load_alpaca_config() -> AlpacaConfig:
    api_key = os.getenv("ALPACA_KEY", "").strip()
    api_secret = os.getenv("ALPACA_SECRET", "").strip()
    if not api_key or not api_secret:
        raise ValueError("Missing Alpaca credentials in ALPACA_KEY/ALPACA_SECRET")
    return AlpacaConfig(api_key=api_key, api_secret=api_secret)


def _ensure_supported_symbol(symbol: str) -> str:
    symbol_norm = symbol.upper().strip()
    if symbol_norm not in SUPPORTED_STOCKS:
        raise ValueError(f"Unsupported US stock symbol: {symbol_norm}")
    return symbol_norm


class AlpacaClient:
    def __init__(self, max_rpm: int = 200):
        self._config = _load_alpaca_config()
        self._data_client = StockHistoricalDataClient(
            api_key=self._config.api_key,
            secret_key=self._config.api_secret
        )
        self._trading_client = TradingClient(
            api_key=self._config.api_key,
            secret_key=self._config.api_secret,
            paper=True
        )
        self._limiter = RateLimiter(max_calls=max_rpm, window_seconds=60)
        self._max_rpm = max_rpm

    def _wait(self) -> None:
        logger.debug("Alpaca rate limiter: max_rpm=%s", self._max_rpm)
        self._limiter.wait_for_slot()

    def _request_feed_kwargs(self) -> Dict[str, Any]:
        """
        Controlled by config:
        - True: use IEX feed explicitly
        - False: do not pass feed parameter
        """
        if AgentConfig.ALPACA_USE_IEX_FEED:
            return {"feed": DataFeed.IEX}
        return {}

    def get_last_price(self, symbol: str) -> Optional[float]:
        try:
            symbol_norm = _ensure_supported_symbol(symbol)
            logger.info("Alpaca latest trade request: %s", symbol_norm)
            self._wait()
            if delta_t_minutes() > 0:
                end_dt = now_utc()
                start_dt = end_dt - timedelta(minutes=10)
                req_kwargs = self._request_feed_kwargs()
                req = StockBarsRequest(
                    symbol_or_symbols=[symbol_norm],
                    timeframe=TimeFrame(1, TimeFrameUnit.Minute),
                    start=start_dt,
                    end=end_dt,
                    limit=10,
                    **req_kwargs,
                )
                bars = self._data_client.get_stock_bars(req)
                if hasattr(bars, "data"):
                    bar_list = bars.data.get(symbol_norm, [])
                elif isinstance(bars, dict):
                    bar_list = bars.get(symbol_norm, [])
                else:
                    try:
                        bar_list = bars[symbol_norm]
                    except Exception:
                        bar_list = []
                if bar_list:
                    last_bar = bar_list[-1]
                    price = getattr(last_bar, "close", None)
                    logger.info("Alpaca delayed price success: %s price=%s", symbol_norm, price)
                    return float(price) if price is not None else None
                logger.warning("Alpaca delayed price empty, fallback to latest trade: %s", symbol_norm)

            req_kwargs = self._request_feed_kwargs()
            req = StockLatestTradeRequest(
                symbol_or_symbols=[symbol_norm],
                **req_kwargs,
            )
            resp = self._data_client.get_stock_latest_trade(req)
            trade = resp.get(symbol_norm)
            if not trade:
                logger.warning("Alpaca latest trade empty: %s", symbol_norm)
                return None
            price = getattr(trade, "price", None)
            logger.info("Alpaca latest trade success: %s price=%s", symbol_norm, price)
            return float(price) if price is not None else None
        except Exception as e:
            logger.error(f"Error fetching Alpaca price for {symbol}: {e}")
            return None

    def get_last_close_price(self, symbol: str, lookback_days: int = 10) -> Optional[float]:
        """Get latest available daily close price for a US symbol.

        This is used for valuation/settlement when the US market is closed.
        """
        try:
            symbol_norm = _ensure_supported_symbol(symbol)
            end_dt = now_utc()
            start_dt = end_dt - timedelta(days=max(2, int(lookback_days)))

            logger.info(
                "Alpaca latest close request: %s start=%s end=%s",
                symbol_norm,
                start_dt.isoformat(),
                end_dt.isoformat(),
            )
            self._wait()
            req_kwargs = self._request_feed_kwargs()
            req = StockBarsRequest(
                symbol_or_symbols=[symbol_norm],
                timeframe=TimeFrame(1, TimeFrameUnit.Day),
                start=start_dt,
                end=end_dt,
                limit=max(2, int(lookback_days)),
                **req_kwargs,
            )
            bars = self._data_client.get_stock_bars(req)
            if hasattr(bars, "data"):
                bar_list = bars.data.get(symbol_norm, [])
            elif isinstance(bars, dict):
                bar_list = bars.get(symbol_norm, [])
            else:
                try:
                    bar_list = bars[symbol_norm]
                except Exception:
                    bar_list = []

            if not bar_list:
                logger.warning("Alpaca latest close empty: %s", symbol_norm)
                return None

            # Keep deterministic by timestamp, then use the latest bar close.
            bar_list = sorted(
                bar_list,
                key=lambda b: getattr(b, "timestamp", datetime.min.replace(tzinfo=timezone.utc)),
            )
            last_bar = bar_list[-1]
            close_price = getattr(last_bar, "close", None)
            logger.info("Alpaca latest close success: %s close=%s", symbol_norm, close_price)
            return float(close_price) if close_price is not None else None
        except Exception as e:
            logger.error(f"Error fetching Alpaca latest close for {symbol}: {e}")
            return None

    def get_kline_data(
        self,
        symbol: str,
        period: str = "1d",
        count: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        try:
            symbol_norm = _ensure_supported_symbol(symbol)
            timeframe = _map_timeframe(period)

            if end_time:
                end_dt = datetime.fromtimestamp(end_time / 1000, tz=timezone.utc)
            else:
                end_dt = now_utc()

            if start_time:
                start_dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
            else:
                start_dt = _estimate_start_time(end_dt, timeframe, count)

            logger.info(
                "Alpaca bars request: %s timeframe=%s start=%s end=%s limit=%s",
                symbol_norm,
                timeframe,
                start_dt.isoformat(),
                end_dt.isoformat(),
                count,
            )
            self._wait()
            req_kwargs = self._request_feed_kwargs()
            req = StockBarsRequest(
                symbol_or_symbols=[symbol_norm],
                timeframe=timeframe,
                start=start_dt,
                end=end_dt,
                limit=count,
                **req_kwargs,
            )
            bars = self._data_client.get_stock_bars(req)
            if hasattr(bars, "data"):
                bar_list = bars.data.get(symbol_norm, [])
            elif isinstance(bars, dict):
                bar_list = bars.get(symbol_norm, [])
            else:
                try:
                    bar_list = bars[symbol_norm]
                except Exception:
                    bar_list = []
            if not bar_list:
                logger.warning("Alpaca bars empty: %s timeframe=%s", symbol_norm, timeframe)

            result: List[Dict[str, Any]] = []
            for bar in bar_list:
                ts = bar.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                else:
                    ts = ts.astimezone(timezone.utc)
                open_price = float(bar.open) if bar.open is not None else None
                close_price = float(bar.close) if bar.close is not None else None
                change = (close_price - open_price) if (open_price is not None and close_price is not None) else 0
                percent = (change / open_price * 100) if open_price else 0
                result.append({
                    "timestamp": int(ts.timestamp()),
                    "datetime_str": ts.isoformat(),
                    "open": open_price,
                    "high": float(bar.high) if bar.high is not None else None,
                    "low": float(bar.low) if bar.low is not None else None,
                    "close": close_price,
                    "volume": float(bar.volume) if bar.volume is not None else None,
                    "amount": float(bar.volume * bar.close) if bar.volume and bar.close else None,
                    "change": float(change),
                    "percent": float(percent),
                })

            logger.info("Alpaca bars success: %s count=%s", symbol_norm, len(result))
            return result
        except Exception as e:
            logger.error(f"Error fetching Alpaca klines for {symbol}: {e}")
            return []

    def get_market_status(self, symbol: str) -> Dict[str, Any]:
        try:
            symbol_norm = _ensure_supported_symbol(symbol)
            logger.info("Alpaca market status request: %s", symbol_norm)
            self._wait()
            clock = self._trading_client.get_clock()
            is_open = bool(getattr(clock, "is_open", False))
            ts = getattr(clock, "timestamp", now_utc())
            ts = ts if isinstance(ts, datetime) else now_utc()
            logger.info(
                "Alpaca market status success: %s is_open=%s timestamp=%s",
                symbol_norm,
                is_open,
                ts.isoformat(),
            )
            return {
                "market_status": "OPEN" if is_open else "CLOSED",
                "is_trading": is_open,
                "symbol": symbol_norm,
                "exchange": "Alpaca",
                "market_type": "us_stock",
                "timestamp": int(ts.timestamp()),
                "current_time": ts.isoformat(),
            }
        except Exception as e:
            logger.error(f"Error getting Alpaca market status for {symbol}: {e}")
            return {
                "market_status": "ERROR",
                "is_trading": False,
                "error": str(e),
            }


def _map_timeframe(period: str) -> TimeFrame:
    period_norm = (period or "1d").lower()
    mapping = {
        "1m": TimeFrame(1, TimeFrameUnit.Minute),
        "5m": TimeFrame(5, TimeFrameUnit.Minute),
        "15m": TimeFrame(15, TimeFrameUnit.Minute),
        "30m": TimeFrame(30, TimeFrameUnit.Minute),
        "1h": TimeFrame(1, TimeFrameUnit.Hour),
        "4h": TimeFrame(4, TimeFrameUnit.Hour),
        "1d": TimeFrame(1, TimeFrameUnit.Day),
    }
    return mapping.get(period_norm, TimeFrame(1, TimeFrameUnit.Day))


def _estimate_start_time(end_dt: datetime, timeframe: TimeFrame, count: int) -> datetime:
    if timeframe.unit == TimeFrameUnit.Minute:
        delta = timedelta(minutes=timeframe.amount * count)
    elif timeframe.unit == TimeFrameUnit.Hour:
        delta = timedelta(hours=timeframe.amount * count)
    elif timeframe.unit == TimeFrameUnit.Day:
        delta = timedelta(days=timeframe.amount * count)
    else:
        delta = timedelta(days=count)
    return end_dt - delta


def _alpaca_feed_kwargs() -> Dict[str, Any]:
    if not ALPACA_US_FEED_ENABLED:
        return {}
    feed = (ALPACA_US_FEED or "").strip()
    if not feed:
        return {}
    return {"feed": feed}


_alpaca_client: Optional[AlpacaClient] = None
_alpaca_client_lock = Lock()


def _get_alpaca_client() -> AlpacaClient:
    global _alpaca_client
    if _alpaca_client is not None:
        return _alpaca_client
    with _alpaca_client_lock:
        if _alpaca_client is None:
            _alpaca_client = AlpacaClient()
        return _alpaca_client


def get_last_price_from_alpaca(symbol: str) -> Optional[float]:
    return _get_alpaca_client().get_last_price(symbol)


def get_last_close_price_from_alpaca(symbol: str) -> Optional[float]:
    return _get_alpaca_client().get_last_close_price(symbol)


def get_kline_data_from_alpaca(
    symbol: str,
    period: str = "1d",
    count: int = 100,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None
) -> List[Dict[str, Any]]:
    return _get_alpaca_client().get_kline_data(symbol, period, count, start_time, end_time)


def get_market_status_from_alpaca(symbol: str) -> Dict[str, Any]:
    return _get_alpaca_client().get_market_status(symbol)


def get_all_supported_symbols() -> List[str]:
    return list(SUPPORTED_STOCKS)
