"""Central symbol/market registry used by refactored market and trading paths."""

from __future__ import annotations

from dataclasses import dataclass

from benchmark.contracts import Market
from services.trading_symbols import AI_TRADING_SYMBOLS


CRYPTO_SYMBOLS = tuple(str(symbol).upper() for symbol in AI_TRADING_SYMBOLS)
US_SYMBOLS = ("AAPL", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "PG", "JNJ", "UNH", "JPM", "V", "BA", "XOM", "NEE", "AMT", "PLD", "LIN")


@dataclass(frozen=True)
class SymbolMarket:
    symbol: str
    market: Market


def normalize_market(market: str | Market | None) -> Market:
    if isinstance(market, Market):
        return market
    market_upper = str(market or "CRYPTO").strip().upper()
    if market_upper in {"US", "STOCK", "STOCKS"}:
        return Market.US
    if market_upper in {"CRYPTO", "HYPERLIQUID"}:
        return Market.CRYPTO
    raise ValueError("market must be CRYPTO or US")


def normalize_symbol(symbol: str) -> str:
    symbol_norm = str(symbol or "").strip().upper()
    if not symbol_norm:
        raise ValueError("symbol is required")
    if "." in symbol_norm:
        raise ValueError("symbol must not include market suffix")
    return symbol_norm


def resolve_symbol_market(symbol: str, market: str | Market | None = None) -> SymbolMarket:
    symbol_norm = normalize_symbol(symbol)
    market_norm = normalize_market(market)
    validate_supported(symbol_norm, market_norm)
    return SymbolMarket(symbol=symbol_norm, market=market_norm)


def validate_supported(symbol: str, market: Market) -> None:
    if market is Market.US:
        if "/" in symbol or ":" in symbol:
            raise ValueError(f"Invalid US symbol '{symbol}': crypto pair format is not allowed")
        if symbol not in US_SYMBOLS:
            raise ValueError(f"Unsupported US stock symbol: {symbol}")
    if market is Market.CRYPTO and symbol in US_SYMBOLS:
        raise ValueError(f"Invalid market for symbol '{symbol}': use market='US'")
    if market is Market.CRYPTO and symbol not in CRYPTO_SYMBOLS:
        raise ValueError(f"Unsupported CRYPTO symbol: {symbol}")


def infer_market(symbol: str, explicit_market: str | Market | None = None) -> Market:
    if explicit_market:
        return normalize_market(explicit_market)
    symbol_norm = normalize_symbol(symbol)
    return Market.US if symbol_norm in US_SYMBOLS else Market.CRYPTO


__all__ = [
    "CRYPTO_SYMBOLS",
    "SymbolMarket",
    "US_SYMBOLS",
    "infer_market",
    "normalize_market",
    "normalize_symbol",
    "resolve_symbol_market",
    "validate_supported",
]
