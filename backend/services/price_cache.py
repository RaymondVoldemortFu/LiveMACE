"""
Price caching service to reduce API calls and improve performance
"""

import logging
from threading import Lock
from decimal import Decimal
from typing import Dict, Optional, Tuple

from benchmark.infrastructure.market.symbols import resolve_symbol_market
from benchmark.providers import Freshness, PriceResult
from services.time_source import now_timestamp, now_utc
logger = logging.getLogger(__name__)


class PriceCache:
    """Simple in-memory price cache with TTL"""
    
    def __init__(self, ttl_seconds: int = 30):
        self.cache: Dict[Tuple[str, str], Tuple[PriceResult, float]] = {}
        self.ttl_seconds = ttl_seconds
        self.lock = Lock()
    
    def get(self, symbol: str, market: str) -> Optional[float]:
        """Get cached price if still valid"""
        resolved = resolve_symbol_market(symbol, market)
        key = (resolved.symbol, resolved.market.value)
        current_time = now_timestamp()
        
        with self.lock:
            if key in self.cache:
                result, timestamp = self.cache[key]
                if current_time - timestamp < self.ttl_seconds:
                    logger.debug(f"Cache hit for {resolved.symbol}.{resolved.market.value}: {result.value}")
                    return float(result.value) if result.value is not None else None
                else:
                    # Remove expired entry
                    del self.cache[key]
                    logger.debug(f"Cache expired for {resolved.symbol}.{resolved.market.value}")
        
        return None
    
    def set(self, symbol: str, market: str, price: float):
        """Cache a price with current timestamp"""
        resolved = resolve_symbol_market(symbol, market)
        self.set_result(
            resolved.symbol,
            resolved.market.value,
            PriceResult(
                value=Decimal(str(price)),
                as_of=now_utc(),
                source="core.market.legacy",
                freshness=Freshness.FRESH,
            ),
        )

    def get_result(self, symbol: str, market: str) -> PriceResult | None:
        resolved = resolve_symbol_market(symbol, market)
        key = (resolved.symbol, resolved.market.value)
        current_time = now_timestamp()
        with self.lock:
            entry = self.cache.get(key)
            if entry is None:
                return None
            result, timestamp = entry
            if current_time - timestamp >= self.ttl_seconds:
                del self.cache[key]
                return None
            return result

    def set_result(self, symbol: str, market: str, result: PriceResult) -> None:
        resolved = resolve_symbol_market(symbol, market)
        key = (resolved.symbol, resolved.market.value)
        with self.lock:
            self.cache[key] = (result, now_timestamp())
            logger.debug(f"Cached price for {resolved.symbol}.{resolved.market.value}: {result.value}")

    def clear(self) -> None:
        with self.lock:
            self.cache.clear()
    
    def clear_expired(self):
        """Remove all expired entries"""
        current_time = now_timestamp()
        expired_keys = []
        
        with self.lock:
            for key, (_, timestamp) in self.cache.items():
                if current_time - timestamp >= self.ttl_seconds:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self.cache[key]
        
        if expired_keys:
            logger.debug(f"Cleared {len(expired_keys)} expired cache entries")
    
    def get_cache_stats(self) -> Dict:
        """Get cache statistics"""
        current_time = now_timestamp()
        total_entries = 0
        valid_entries = 0
        
        with self.lock:
            total_entries = len(self.cache)
            for _, timestamp in self.cache.values():
                if current_time - timestamp < self.ttl_seconds:
                    valid_entries += 1
        
        return {
            "total_entries": total_entries,
            "valid_entries": valid_entries,
            "ttl_seconds": self.ttl_seconds
        }


# Global price cache instance
price_cache = PriceCache(ttl_seconds=30)  # Cache prices for 30 seconds


def get_cached_price(symbol: str, market: str = "CRYPTO") -> Optional[float]:
    """Get price from cache if available"""
    return price_cache.get(symbol, market)


def cache_price(symbol: str, market: str, price: float):
    """Cache a price"""
    price_cache.set(symbol, market, price)


def clear_expired_prices():
    """Clear expired price entries"""
    price_cache.clear_expired()


def get_price_cache_stats() -> Dict:
    """Get cache statistics"""
    return price_cache.get_cache_stats()
