from pydantic import BaseModel
from typing import Dict, List
import os


# Default crypto symbols used by AI trading and baselines.
# Keep this as a single source of truth; services should import it from here
# (directly or via services.trading_symbols for backward compatibility).
AI_TRADING_SYMBOLS: List[str] = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]


class MarketConfig(BaseModel):
    market: str
    min_commission: float
    commission_rate: float
    exchange_rate: float
    min_order_quantity: int = 1
    lot_size: int = 1


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
}


class SecuritySettings(BaseModel):
    """Centralized security-related runtime settings."""

    api_key_cipher_key: str = (os.getenv("API_KEY_CIPHER_KEY") or "").strip()
    api_key_fallback_env_var: str = (os.getenv("API_KEY_FALLBACK_ENV_VAR") or "API_KEY").strip()


SECURITY_SETTINGS = SecuritySettings()
