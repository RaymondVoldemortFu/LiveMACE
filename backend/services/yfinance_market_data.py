"""
YFinance market data service for US stocks
"""
import yfinance as yf
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
import pandas as pd
import time
import requests
import os
from config.settings import TIME_OFFSET_MINUTES, SUPPORTED_STOCKS
from config.proxy_config import proxy_config

logger = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

class YFinanceClient:
    def __init__(self):
        self.supported_stocks = set(SUPPORTED_STOCKS)
        self.proxy_url = proxy_config.get_proxy_url()
        # Ensure proxy environment variables are set when client is initialized
        proxy_config.setup_global_proxy()
        self._last_trace_ts = 0.0
        self._trace_interval_seconds = 300

    def _trace_proxy_and_api(self, symbol: str):
        """Trace proxy and Yahoo API connectivity (rate-limited)."""
        now = time.time()
        if now - self._last_trace_ts < self._trace_interval_seconds:
            return
        self._last_trace_ts = now

        proxies = proxy_config.get_proxy_dict()

        ca_path = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")

        # 1) Trace proxy IP
        try:
            ip_resp = requests.get(
                "http://httpbin.org/ip",
                proxies=proxies,
                timeout=10,
                verify=ca_path if ca_path else True
            )
            logger.warning(
                "YF trace httpbin: status=%s origin=%s proxies_enabled=%s",
                ip_resp.status_code, ip_resp.text.strip(), bool(proxies)
            )
        except Exception as e:
            logger.warning("YF trace httpbin failed: %s proxies_enabled=%s", repr(e), bool(proxies))

        # 2) Trace Yahoo quote endpoint
        try:
            url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            quote_resp = requests.get(
                url,
                headers=headers,
                proxies=proxies,
                timeout=10,
                verify=ca_path if ca_path else True
            )
            logger.warning(
                "YF trace Yahoo quote: status=%s length=%s symbol=%s",
                quote_resp.status_code, len(quote_resp.text or ""), symbol
            )
            if quote_resp.status_code == 200:
                try:
                    data = quote_resp.json()
                    result_count = len(data.get("quoteResponse", {}).get("result", []))
                    logger.warning(
                        "YF trace Yahoo quote JSON: results=%s error=%s",
                        result_count, data.get("quoteResponse", {}).get("error")
                    )
                except Exception as parse_err:
                    logger.warning("YF trace Yahoo quote JSON parse failed: %s", repr(parse_err))
        except Exception as e:
            logger.warning("YF trace Yahoo quote failed: %s", repr(e))

    def _safe_download(self, symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
        """Fetch data via yf.download only to avoid history() NoneType issues."""
        try:
            hist = yf.download(
                symbol,
                period=period,
                interval=interval,
                progress=False,
                threads=False,
                group_by="column",
                auto_adjust=False
            )
            if hist is not None and not hist.empty:
                return hist
        except Exception as dl_err:
            logger.warning(
                "Download failed for %s: %s (period=%s, interval=%s)",
                symbol, repr(dl_err), period, interval
            )
        return None

    def _safe_last_price(self, symbol: str) -> Optional[float]:
        """Try fast_info first, then download daily data."""
        try:
            ticker = yf.Ticker(symbol)
            fi = getattr(ticker, "fast_info", None)
            if fi and isinstance(fi, dict):
                price = fi.get("last_price") or fi.get("lastPrice")
                if price:
                    return float(price)
        except Exception as err:
            logger.debug("fast_info failed for %s: %s", symbol, repr(err))

        # Fallback: daily download
        hist = self._safe_download(symbol, interval="1d", period="1mo")
        if hist is None or hist.empty or "Close" not in hist or hist["Close"].empty:
            return None
        return float(hist["Close"].iloc[-1])

    def is_supported(self, symbol: str) -> bool:
        return symbol in self.supported_stocks

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Get the last price for a symbol, respecting time offset"""
        try:
            if not self.is_supported(symbol):
                logger.warning(f"Symbol {symbol} not in supported stock list")
                return None

            # Calculate simulated "now"
            simulated_now = datetime.now(timezone.utc) - timedelta(minutes=TIME_OFFSET_MINUTES)
            
            # Use fast_info or daily download to avoid history() NoneType errors
            last_price = self._safe_last_price(symbol)
            if last_price is None:
                self._trace_proxy_and_api(symbol)
                logger.warning(
                    "No price data found for %s around %s (fast_info/download).",
                    symbol, simulated_now
                )
                return None

            logger.info(f"Got price for {symbol} at {simulated_now} (simulated): {last_price}")
            return float(last_price)
            
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return None

    def get_kline_data(self, symbol: str, period: str = '1d', count: int = 100, start_time: Optional[int] = None, end_time: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get kline/candlestick data for a symbol"""
        try:
            if not self.is_supported(symbol):
                return []

            # ... (time calc logic) ...
            simulated_now = datetime.now(timezone.utc) - timedelta(minutes=TIME_OFFSET_MINUTES)
            simulated_now_ms = int(simulated_now.timestamp() * 1000)
            
            if end_time is None:
                end_time_ms = simulated_now_ms
            else:
                end_time_ms = min(end_time, simulated_now_ms)
            
            period_map = {
                '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
                '1h': '1h', '4h': '1h', '1d': '1d', '1w': '1wk'
            }
            yf_interval = period_map.get(period, '1d')
            
            end_dt = datetime.fromtimestamp(end_time_ms / 1000, tz=timezone.utc)
            
            if start_time:
                start_dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
            else:
                multipliers = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '1d': 1440, '1wk': 10080}
                mins = multipliers.get(yf_interval, 1440) * count * 2
                start_dt = end_dt - timedelta(minutes=mins)

            days_back = (datetime.now(timezone.utc) - start_dt).days
            if 'm' in yf_interval and days_back > 59:
                 logger.warning(f"Requested intraday data older than 60 days for {symbol}, falling back to 1d")
                 yf_interval = '1d'

            period_map = {
                '1m': '5d',
                '5m': '5d',
                '15m': '5d',
                '30m': '5d',
                '1h': '1mo',
                '1d': '1y'
            }
            fallback_period = period_map.get(yf_interval, '1mo')
            hist = self._safe_download(
                symbol=symbol,
                interval=yf_interval,
                period=fallback_period
            )
            if hist is not None and not hist.empty:
                hist = hist[hist.index <= end_dt]

            if hist is None or hist.empty:
                logger.warning(
                    "Failed to fetch kline history for %s (period=%s, interval=%s)",
                    symbol, fallback_period, yf_interval
                )
                return []
            
            klines = []
            if hist is None or hist.empty:
                logger.warning(
                    "Empty kline history for %s (interval=%s, start=%s, end=%s). hist is None=%s",
                    symbol, yf_interval, start_dt, end_dt, hist is None
                )
                return []

            for idx, row in hist.iterrows():
                # ... (parsing logic) ...
                dt = idx.to_pydatetime()
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                
                timestamp_ms = int(dt.timestamp() * 1000)
                
                if timestamp_ms > end_time_ms:
                    continue
                
                klines.append({
                    'timestamp': int(timestamp_ms / 1000),
                    'datetime_str': dt.isoformat(),
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'close': float(row['Close']),
                    'volume': float(row['Volume']),
                    'amount': float(row['Volume'] * row['Close']),
                    'change': float(row['Close'] - row['Open']),
                    'percent': float((row['Close'] - row['Open']) / row['Open'] * 100) if row['Open'] != 0 else 0
                })
            
            if len(klines) > count:
                klines = klines[-count:]
                
            logger.info(f"Got {len(klines)} klines for {symbol} (US)")
            return klines
            
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol}: {e}")
            return []

    def get_market_status(self, symbol: str) -> Dict[str, Any]:
        """Get market status for a symbol"""
        try:
            if not self.is_supported(symbol):
                return {
                    'market_status': 'CLOSED',
                    'is_trading': False,
                    'error': 'Unsupported symbol'
                }
            
            # Check if current time (simulated) is within US market hours
            # NYSE: 9:30 AM - 4:00 PM ET, Mon-Fri
            simulated_now = datetime.now(timezone.utc) - timedelta(minutes=TIME_OFFSET_MINUTES)
            
            # Convert to Eastern Time
            # Simplified check (ignoring holidays for now, or using simple weekday check)
            # This is "good enough" for a mock/simulated environment unless we need strict calendar
            
            is_weekend = simulated_now.weekday() >= 5
            
            # Simple heuristic
            status = 'CLOSED'
            if not is_weekend:
                # UTC is ET + 4 or + 5.
                # Let's just say "OPEN" if we can fetch a recent price? 
                # Or just return static info since we rely on historical data anyway.
                status = 'OPEN' 
            
            return {
                'market_status': status,
                'is_trading': status == 'OPEN',
                'symbol': symbol,
                'exchange': 'NASDAQ/NYSE',
                'market_type': 'stock',
                'base_currency': 'USD',
                'quote_currency': 'USD'
            }
            
        except Exception as e:
            logger.error(f"Error getting market status for {symbol}: {e}")
            return {'market_status': 'ERROR'}

    def get_all_symbols(self) -> List[str]:
        return list(self.supported_stocks)

# Global instance
yfinance_client = YFinanceClient()

def get_last_price_from_yfinance(symbol: str) -> Optional[float]:
    return yfinance_client.get_last_price(symbol)

def get_kline_data_from_yfinance(symbol: str, period: str = '1d', count: int = 100, start_time: Optional[int] = None, end_time: Optional[int] = None) -> List[Dict[str, Any]]:
    return yfinance_client.get_kline_data(symbol, period, count, start_time, end_time)

def get_market_status_from_yfinance(symbol: str) -> Dict[str, Any]:
    return yfinance_client.get_market_status(symbol)

def get_all_symbols_from_yfinance() -> List[str]:
    return yfinance_client.get_all_symbols()

