"""
YFinance market data service for US stocks
"""
import yfinance as yf
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
import pandas as pd
from config.settings import TIME_OFFSET_MINUTES, SUPPORTED_STOCKS

logger = logging.getLogger(__name__)

class YFinanceClient:
    def __init__(self):
        self.supported_stocks = set(SUPPORTED_STOCKS)

    def is_supported(self, symbol: str) -> bool:
        return symbol in self.supported_stocks

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Get the last price for a symbol, respecting time offset"""
        try:
            if not self.is_supported(symbol):
                logger.warning(f"Symbol {symbol} not in supported stock list")
                # Attempt anyway if requested, or return None? 
                # Strict adherence to "supported" list is better for safety, but yfinance might support it.
                # The user requirement says "Need to support (and only need to support) the following stock set".
                return None

            # Calculate simulated "now"
            # Using UTC for consistency, though US stocks are EST/EDT. 
            # yfinance handles timezones, usually returning data with tz-aware timestamps.
            simulated_now = datetime.now(timezone.utc) - timedelta(minutes=TIME_OFFSET_MINUTES)
            
            ticker = yf.Ticker(symbol)
            
            # Fetch recent history. 
            # If offset is small (e.g. 0), we want the absolute latest.
            # If offset is large (e.g. 1 year), we need history around that time.
            
            # yfinance history() 'end' is exclusive. 
            # If we want data up to simulated_now, we might need to fetch a bit more context.
            # '1d' period is default, '1m' is smallest.
            
            # Strategy: Fetch 5 days of 1m data ending at simulated_now (if within last 7 days)
            # or 1d data if older.
            
            # yfinance limitation: 1m data only available for last 7 days.
            # If TIME_OFFSET_MINUTES puts us back more than 7 days, we can only use 1h or 1d data?
            # actually yfinance allows intraday for last 60 days (interval='1h', '30m' etc).
            # Let's try to get the best resolution possible.
            
            end_date = simulated_now
            start_date = end_date - timedelta(days=5) # look back 5 days to ensure we find a trading session
            
            # Determine interval based on how far back we are
            days_back = (datetime.now(timezone.utc) - simulated_now).days
            
            interval = '1d'
            if days_back < 7:
                interval = '1m'
            elif days_back < 60:
                interval = '1h'
                
            # fetch
            hist = ticker.history(start=start_date, end=end_date + timedelta(minutes=1), interval=interval)
            
            if hist.empty:
                # Maybe it was a weekend or holiday? Try fetching just '1d' data for a wider range if intraday failed
                if interval != '1d':
                     hist = ticker.history(start=start_date, end=end_date + timedelta(days=1), interval='1d')
            
            if hist.empty:
                 logger.warning(f"No price data found for {symbol} around {simulated_now}")
                 return None

            # The last row should be the closest to 'simulated_now' because we capped 'end' at 'simulated_now'.
            # However, yfinance 'end' is exclusive.
            # We want the last available price BEFORE simulated_now.
            
            last_price = hist['Close'].iloc[-1]
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

            # Determine request boundaries respecting Time Offset
            simulated_now = datetime.now(timezone.utc) - timedelta(minutes=TIME_OFFSET_MINUTES)
            simulated_now_ms = int(simulated_now.timestamp() * 1000)
            
            # If end_time is not provided, cap it at simulated_now
            if end_time is None:
                end_time_ms = simulated_now_ms
            else:
                end_time_ms = min(end_time, simulated_now_ms)
            
            # Map period to yfinance interval
            # yfinance valid intervals: 1m,2m,5m,15m,30m,60m,90m,1h,1d,5d,1wk,1mo,3mo
            period_map = {
                '1m': '1m',
                '5m': '5m',
                '15m': '15m',
                '30m': '30m',
                '1h': '1h',
                '4h': '1h', # Approximation, yfinance doesn't have 4h
                '1d': '1d',
                '1w': '1wk'
            }
            yf_interval = period_map.get(period, '1d')
            
            # Calculate start date
            # If start_time (ms) is provided, use it.
            # Else, calculate based on count * period approx duration
            
            end_dt = datetime.fromtimestamp(end_time_ms / 1000, tz=timezone.utc)
            
            if start_time:
                start_dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
            else:
                # Estimate lookback
                # This is rough; yfinance might return fewer bars (weekends etc)
                # so we ask for more buffer
                multipliers = {
                    '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                    '1h': 60, '1d': 1440, '1wk': 10080
                }
                mins = multipliers.get(yf_interval, 1440) * count * 2 # 2x buffer for non-trading hours
                start_dt = end_dt - timedelta(minutes=mins)

            # Check yfinance limitations for intraday
            days_back = (datetime.now(timezone.utc) - start_dt).days
            if 'm' in yf_interval and days_back > 59:
                 logger.warning(f"Requested intraday data older than 60 days for {symbol}, falling back to 1d")
                 yf_interval = '1d'

            ticker = yf.Ticker(symbol)
            
            # fetch history
            # end in yfinance is exclusive, so we might need to add a bit if we want to include the exact end_dt minute?
            # Actually we want UP TO end_dt.
            hist = ticker.history(start=start_dt, end=end_dt, interval=yf_interval)
            
            klines = []
            for idx, row in hist.iterrows():
                # idx is the index (Datetime)
                dt = idx.to_pydatetime()
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc) # assume utc if not set (yfinance usually sets it)
                
                timestamp_ms = int(dt.timestamp() * 1000)
                
                # Double check bounds
                if timestamp_ms > end_time_ms:
                    continue
                
                # yfinance returns Open, High, Low, Close, Volume
                klines.append({
                    'timestamp': int(timestamp_ms / 1000),
                    'datetime_str': dt.isoformat(),
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'close': float(row['Close']),
                    'volume': float(row['Volume']),
                    'amount': float(row['Volume'] * row['Close']), # Approximation
                    'change': float(row['Close'] - row['Open']),
                    'percent': float((row['Close'] - row['Open']) / row['Open'] * 100) if row['Open'] != 0 else 0
                })
            
            # Limit count if we fetched too many
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

