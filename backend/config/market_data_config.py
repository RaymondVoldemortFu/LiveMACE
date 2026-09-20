"""US data requests explicitly use the IEX subscription."""

from datetime import datetime
import math
import os

ALPACA_US_FEED_ENABLED = True
ALPACA_US_FEED = "iex"


def alpaca_feed_kwargs():
    """Shared feed selection for SDK and HTTP market-data requests."""
    return {"feed": ALPACA_US_FEED}


def alpaca_quote_is_fresh(as_of: datetime | None, now: datetime) -> bool:
    """Judge source quote time, never request/cache insertion time."""
    max_age = float(os.getenv("ALPACA_MAX_QUOTE_AGE_SECONDS", "120"))
    if not math.isfinite(max_age) or max_age <= 0:
        raise ValueError("ALPACA_MAX_QUOTE_AGE_SECONDS must be positive and finite")
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        return False
    age = (now - as_of).total_seconds()
    return 0 <= age <= max_age
