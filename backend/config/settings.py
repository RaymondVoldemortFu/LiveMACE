from pydantic import BaseModel
from typing import Dict


class MarketConfig(BaseModel):
    market: str
    min_commission: float
    commission_rate: float
    exchange_rate: float
    min_order_quantity: int = 1
    lot_size: int = 1


# Time offset configuration for simulation (in minutes)
# Positive value means the system perceives time as (Real Time - Offset)
# This affects both the timestamp of "current" data and the data fetching logic
TIME_OFFSET_MINUTES: int = 30


#  default configs for CRYPTO markets
DEFAULT_TRADING_CONFIGS: Dict[str, MarketConfig] = {
    "CRYPTO": MarketConfig(
        market="CRYPTO",
        min_commission=0.1,  # $0.1 minimum commission for crypto
        commission_rate=0.001,  # 0.1% commission rate (typical for crypto)
        exchange_rate=1.0,  # USD base
        min_order_quantity=1,  # Can trade fractional amounts
        lot_size=1,
    ),
    "US": MarketConfig(
        market="US",
        min_commission=1.0,  # $1.0 minimum
        commission_rate=0.0003, # Low fee
        exchange_rate=1.0,
        min_order_quantity=1,
        lot_size=1,
    )
}

SUPPORTED_STOCKS = [
    # Information Technology
    "AAPL", "MSFT", "NVDA", "AVGO",
    # Communication Services
    "GOOGL", "META", "NFLX",
    # Consumer Discretionary
    "AMZN", "TSLA", "NKE",
    # Consumer Staples
    "PG", "KO", "WMT",
    # Health Care
    "JNJ", "PFE", "UNH",
    # Financials
    "JPM", "BAC", "V",
    # Industrials
    "BA", "CAT", "UNP",
    # Energy
    "XOM", "CVX",
    # Utilities
    "NEE", "DUK",
    # Real Estate
    "AMT", "PLD",
    # Materials
    "LIN", "DD"
]
