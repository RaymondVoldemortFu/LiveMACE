"""
Hyperliquid market data service using CCXT
"""
import ccxt
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta
import time
from config.settings import TIME_OFFSET_MINUTES
from config.proxy_config import proxy_config

logger = logging.getLogger(__name__)

class HyperliquidClient:
    def __init__(self):
        self.exchange = None
        self._initialize_exchange()
    
    def _initialize_exchange(self):
        """Initialize CCXT Hyperliquid exchange"""
        try:
            config = {
                'sandbox': False,  # Set to True for testnet
                'enableRateLimit': True,
            }
            
            # Remove direct proxy config if using global env vars
            # CCXT usually picks up env vars automatically, or we can explicity set 'aiohttp_proxy' / 'proxies'
            # But mixing global env vars and constructor config can be tricky.
            # Let's rely on global env vars first if setup_global_proxy is called.
            
            # However, if we want to be explicit:
            # proxies = proxy_config.get_proxy_dict()
            # if proxies:
            #    config['proxies'] = proxies
            
            self.exchange = ccxt.hyperliquid(config)
            logger.info("Hyperliquid exchange initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Hyperliquid exchange: {e}")
            raise

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Get the last price for a symbol"""
        try:
            if not self.exchange:
                self._initialize_exchange()
            
            # Ensure symbol is in CCXT format (e.g., 'BTC/USD')
            formatted_symbol = self._format_symbol(symbol)
            
            if TIME_OFFSET_MINUTES > 0:
                # If offset is set, get price from klines at simulated time
                simulated_now = datetime.now(timezone.utc) - timedelta(minutes=TIME_OFFSET_MINUTES)
                simulated_now_ms = int(simulated_now.timestamp() * 1000)
                
                # Fetch recent klines to find the price at that time
                # Using 1m candles for best precision
                # We need to fetch enough data to cover the offset point
                target_time_ms = simulated_now_ms
                since_ms = target_time_ms - 60000 * 10  # 10 mins back
                
                ohlcv = self.exchange.fetch_ohlcv(formatted_symbol, '1m', since=since_ms, limit=20)
                
                # Find the latest candle that is <= target_time_ms
                target_candle = None
                for candle in ohlcv:
                    # candle[0] is open time. 
                    if candle[0] <= target_time_ms:
                        target_candle = candle
                    else:
                        break
                
                if target_candle:
                    # Use close price of the candle
                    price = target_candle[4]
                    logger.info(f"Got historical price for {formatted_symbol} at {simulated_now}: {price}")
                    return float(price)
                else:
                    logger.warning(f"No historical price found for {formatted_symbol} at {simulated_now}")
                    return None
            else:
                ticker = self.exchange.fetch_ticker(formatted_symbol)
                price = ticker['last']
                
                logger.info(f"Got price for {formatted_symbol}: {price}")
                return float(price) if price else None
            
        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            return None

    def _parse_timeframe_to_ms(self, timeframe: str) -> int:
        """Convert CCXT timeframe string to milliseconds"""
        unit = timeframe[-1]
        try:
            value = int(timeframe[:-1])
        except ValueError:
            return 0
            
        if unit == 'm':
            return value * 60 * 1000
        elif unit == 'h':
            return value * 60 * 60 * 1000
        elif unit == 'd':
            return value * 24 * 60 * 60 * 1000
        elif unit == 'w':
            return value * 7 * 24 * 60 * 60 * 1000
        else:
            return 0

    def get_kline_data(self, symbol: str, period: str = '1d', count: int = 100, start_time: Optional[int] = None, end_time: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get kline/candlestick data for a symbol"""
        try:
            if not self.exchange:
                self._initialize_exchange()
            
            formatted_symbol = self._format_symbol(symbol)
            
            # Handle Time Offset
            if TIME_OFFSET_MINUTES > 0:
                simulated_now = datetime.now(timezone.utc) - timedelta(minutes=TIME_OFFSET_MINUTES)
                simulated_now_ms = int(simulated_now.timestamp() * 1000)
                
                # If end_time is not provided, cap it at simulated_now
                if end_time is None:
                    end_time = simulated_now_ms
                else:
                    # If end_time is provided, ensure it doesn't exceed simulated_now
                    # (Prevent peeking into the future)
                    end_time = min(end_time, simulated_now_ms)
            
            # Map period to CCXT timeframe
            timeframe_map = {
                '1m': '1m',
                '5m': '5m', 
                '15m': '15m',
                '30m': '30m',
                '1h': '1h',
                '4h': '4h', # not sure
                '1d': '1d',
            }
            timeframe = timeframe_map.get(period, '1d')
            
            # Fetch OHLCV data
            # If start_time is provided, use it as 'since'
            since = start_time if start_time else None
            
            # If we have an offset and no start time, we need to calculate 'since' 
            # to ensure we get the data ending at 'end_time' (which is simulated now)
            if TIME_OFFSET_MINUTES > 0 and since is None:
                duration_ms = self._parse_timeframe_to_ms(timeframe)
                if duration_ms > 0:
                    # Calculate required lookback
                    # Add extra buffer (count * 1.5) to account for gaps or partial candles
                    lookback_ms = int(duration_ms * count * 1.5)
                    # end_time is guaranteed to be set if TIME_OFFSET_MINUTES > 0 above
                    since = end_time - lookback_ms
            
            ohlcv = self.exchange.fetch_ohlcv(formatted_symbol, timeframe, since=since, limit=count + 50)
            
            # Convert to our format
            klines = []
            for candle in ohlcv:
                timestamp_ms = candle[0]
                
                # Filter by end_time if provided
                if end_time and timestamp_ms > end_time:
                    continue
                    
                open_price = candle[1]
                high_price = candle[2]
                low_price = candle[3]
                close_price = candle[4]
                volume = candle[5]
                
                # Calculate change
                change = close_price - open_price if open_price else 0
                percent = (change / open_price * 100) if open_price else 0
                
                klines.append({
                    'timestamp': int(timestamp_ms / 1000),  # Convert to seconds
                    'datetime_str': datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat(),
                    'open': float(open_price) if open_price else None,
                    'high': float(high_price) if high_price else None,
                    'low': float(low_price) if low_price else None,
                    'close': float(close_price) if close_price else None,
                    'volume': float(volume) if volume else None,
                    'amount': float(volume * close_price) if volume and close_price else None,
                    'change': float(change),
                    'percent': float(percent),
                })
            
            logger.info(f"Got {len(klines)} klines for {formatted_symbol}")
            return klines
            
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol}: {e}")
            return []

    def get_market_status(self, symbol: str) -> Dict[str, Any]:
        """Get market status for a symbol"""
        try:
            if not self.exchange:
                self._initialize_exchange()
            
            formatted_symbol = self._format_symbol(symbol)
            
            # Hyperliquid is 24/7, but we can check if the market exists
            markets = self.exchange.load_markets()
            market_exists = formatted_symbol in markets
            
            status = {
                'market_status': 'OPEN' if market_exists else 'CLOSED',
                'is_trading': market_exists,
                'symbol': formatted_symbol,
                'exchange': 'Hyperliquid',
                'market_type': 'crypto',
            }
            
            if market_exists:
                market_info = markets[formatted_symbol]
                status.update({
                    'base_currency': market_info.get('base'),
                    'quote_currency': market_info.get('quote'),
                    'active': market_info.get('active', True),
                })
            
            logger.info(f"Market status for {formatted_symbol}: {status['market_status']}")
            return status
            
        except Exception as e:
            logger.error(f"Error getting market status for {symbol}: {e}")
            return {
                'market_status': 'ERROR',
                'is_trading': False,
                'error': str(e)
            }

    def get_all_symbols(self) -> List[str]:
        """Get all available trading symbols"""
        try:
            if not self.exchange:
                self._initialize_exchange()
            
            markets = self.exchange.load_markets()
            symbols = list(markets.keys())
            
            # Filter for USDC pairs (both spot and perpetual)
            usdc_symbols = [s for s in symbols if '/USDC' in s]
            
            # Prioritize mainstream cryptos (perpetual swaps) and popular spot pairs
            mainstream_perps = [s for s in usdc_symbols if any(crypto in s for crypto in ['BTC/', 'ETH/', 'SOL/', 'DOGE/', 'BNB/', 'XRP/'])]
            other_symbols = [s for s in usdc_symbols if s not in mainstream_perps]
            
            # Return mainstream first, then others
            result = mainstream_perps + other_symbols[:50]
            
            logger.info(f"Found {len(usdc_symbols)} USDC trading pairs, returning {len(result)}")
            return result
            
        except Exception as e:
            logger.error(f"Error getting symbols: {e}")
            return ['BTC/USD', 'ETH/USD', 'SOL/USD']  # Fallback popular pairs

    def _format_symbol(self, symbol: str) -> str:
        """Format symbol for CCXT (e.g., 'BTC' -> 'BTC/USDC:USDC')"""
        if '/' in symbol and ':' in symbol:
            return symbol
        elif '/' in symbol:
            # If it's BTC/USDC, convert to BTC/USDC:USDC for Hyperliquid
            return f"{symbol}:USDC"
        
        # For single symbols like 'BTC', check if it's a mainstream crypto
        symbol_upper = symbol.upper()
        mainstream_cryptos = ['BTC', 'ETH', 'SOL', 'DOGE', 'BNB', 'XRP']
        
        if symbol_upper in mainstream_cryptos:
            # Use perpetual swap format for mainstream cryptos
            return f"{symbol_upper}/USDC:USDC"
        else:
            # Use spot format for other cryptos
            return f"{symbol_upper}/USDC"


# Global client instance
hyperliquid_client = HyperliquidClient()


def get_last_price_from_hyperliquid(symbol: str) -> Optional[float]:
    """Get last price from Hyperliquid"""
    return hyperliquid_client.get_last_price(symbol)


def get_kline_data_from_hyperliquid(symbol: str, period: str = '1d', count: int = 100, start_time: Optional[int] = None, end_time: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get kline data from Hyperliquid"""
    return hyperliquid_client.get_kline_data(symbol, period, count, start_time, end_time)


def get_market_status_from_hyperliquid(symbol: str) -> Dict[str, Any]:
    """Get market status from Hyperliquid"""
    return hyperliquid_client.get_market_status(symbol)


def get_all_symbols_from_hyperliquid() -> List[str]:
    """Get all available symbols from Hyperliquid"""
    return hyperliquid_client.get_all_symbols()


def hyperliquid_trade_cost(
    side: str,
    entry_price: float,
    position_size: float,
    leverage: float,
    taker_fee_rate: float = 0.0007,      # Default 0.07%
    interest_rate_hourly: float = 0.0000125,  # Default 0.00125%/hour
    holding_hours: float = 8.0            # Holding period (hours)
) -> dict:
    """
    Calculate fees, interest, liquidation price, etc. for Hyperliquid leveraged positions.

    Args:
      - side: "long" or "short"
      - entry_price: Entry price
      - position_size: Notional value (USD)
      - leverage: Leverage multiple
      - taker_fee_rate: Trading fee rate
      - interest_rate_hourly: Hourly borrowing rate
      - holding_hours: Holding period in hours

    Returns:
      dict containing:
      - initial_margin / maintenance_margin
      - open_fee / close_fee / interest_cost / total_trade_cost
      - liquidation_price / liquidation_fee / liquidation_interest / total_liquidation_cost
    """
    side = side.lower()
    assert side in ("long", "short"), "side must be 'long' or 'short'"

    # === Margin ===
    initial_margin = position_size / leverage
    maintenance_margin = initial_margin / 2  # Typically liquidation line is half of initial margin

    # === 手续费 ===
    open_fee = position_size * taker_fee_rate
    close_fee = position_size * taker_fee_rate
    total_fee = open_fee + close_fee

    # === 利息 ===
    interest_cost = position_size * interest_rate_hourly * holding_hours

    # === 交易总成本 ===
    total_trade_cost = initial_margin + total_fee + interest_cost

    # === 强平价格 ===
    if side == "long":
        liquidation_price = entry_price * leverage / (
            leverage + 1 - (maintenance_margin / initial_margin * leverage)
        )
    else:
        liquidation_price = entry_price * leverage / (
            leverage - 1 + (maintenance_margin / initial_margin * leverage)
        )

    # === 强平费用 ===
    liquidation_fee = position_size * taker_fee_rate
    liquidation_interest = position_size * interest_rate_hourly * holding_hours
    total_liquidation_cost = liquidation_fee + liquidation_interest

    return {
        "side": side,
        "initial_margin": initial_margin,
        "maintenance_margin": maintenance_margin,
        "open_fee": open_fee,
        "close_fee": close_fee,
        "interest_cost": interest_cost,
        "total_trade_cost": total_trade_cost,
        "liquidation_price": liquidation_price,
        "liquidation_fee": liquidation_fee,
        "liquidation_interest": liquidation_interest,
        "total_liquidation_cost": total_liquidation_cost
    }