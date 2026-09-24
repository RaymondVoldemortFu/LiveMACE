"""
Auto Trading Service - Main entry point for automated crypto trading
This file maintains backward compatibility while delegating to split services
"""
import logging

# Import from the new split services

from services.trading_commands import (
    place_ai_driven_crypto_order,
    place_random_crypto_order,
    _get_market_prices,
    _select_side,
    AUTO_TRADE_JOB_ID,
    AI_TRADE_JOB_ID,
    AI_TRADING_SYMBOLS
)


logger = logging.getLogger(__name__)


# Backward compatibility - re-export main functions
# All the actual implementation is now in the split service files

# These constants are kept for backward compatibility and re-exported from trading_commands
# Do not override them here to avoid incorrect ID aliasing
# AUTO_TRADE_JOB_ID and AI_TRADE_JOB_ID are imported above and used as-is
