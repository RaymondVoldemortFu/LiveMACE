"""IEX event timestamps survive the SDK, both production routes and the cache."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from benchmark.contracts import Market
from benchmark.providers import Freshness, PriceResult

NOW = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def alpaca(monkeypatch):
    from alpaca.data.historical import StockHistoricalDataClient
    from services import alpaca_market_data as module

    monkeypatch.setenv("ALPACA_MAX_QUOTE_AGE_SECONDS", "120")
    monkeypatch.setattr(module, "now_utc", lambda: NOW)
    monkeypatch.setattr(module, "delta_t_minutes", lambda: 0)
    sdk = StockHistoricalDataClient("test-only-key", "test-only-secret")
    client = object.__new__(module.AlpacaClient)
    client._data_client = sdk
    client._wait = lambda: None
    monkeypatch.setattr(module, "_get_alpaca_client", lambda: client)
    yield module, client, sdk
    sdk._session.close()


def stub_sdk(monkeypatch, sdk, timestamp, *, bars=False):
    calls = []

    def marketdata(path, params, **kwargs):
        calls.append((path, params))
        if bars:
            return {
                "AAPL": [
                    {
                        "t": timestamp.isoformat(),
                        "o": 99,
                        "h": 101,
                        "l": 98,
                        "c": 100,
                        "v": 10,
                        "n": 1,
                        "vw": 100,
                    }
                ]
            }
        return {
            "AAPL": {
                "t": timestamp.isoformat(),
                "p": 100,
                "s": 1,
                "x": "V",
                "i": 1,
                "c": ["@"],
                "z": "C",
            }
        }

    monkeypatch.setattr(sdk, "_get_marketdata", marketdata)
    return calls


@pytest.mark.parametrize(
    "age,freshness",
    [
        (0, Freshness.FRESH),
        (119, Freshness.FRESH),
        (120, Freshness.FRESH),
        (121, Freshness.STALE),
        (86400, Freshness.STALE),
        (-1, Freshness.STALE),
    ],
)
def test_sdk_trade_source_time_controls_freshness(alpaca, monkeypatch, age, freshness):
    module, client, sdk = alpaca
    timestamp = NOW - timedelta(seconds=age)
    calls = stub_sdk(monkeypatch, sdk, timestamp)
    result = client.get_price_result("AAPL")
    assert result == PriceResult(
        Decimal("100"), timestamp, "core.market.alpaca", freshness, result.error
    )
    assert bool(result.error) is (freshness is Freshness.STALE)
    assert calls[0][0] == "/stocks/trades/latest"
    assert calls[0][1]["feed"] == "iex"
    # The legacy float API remains available for valuation, including old trades.
    assert client.get_last_price("AAPL") == 100.0


def test_sdk_delayed_bar_keeps_its_source_time(alpaca, monkeypatch):
    module, client, sdk = alpaca
    timestamp = NOW - timedelta(minutes=5)
    calls = stub_sdk(monkeypatch, sdk, timestamp, bars=True)
    monkeypatch.setattr(module, "delta_t_minutes", lambda: 15)
    result = client.get_price_result("AAPL")
    assert result.as_of == timestamp
    assert result.freshness is Freshness.STALE
    assert calls[0][0] == "/stocks/bars"
    assert calls[0][1]["feed"] == "iex"


@pytest.mark.parametrize("timestamp", [None, datetime(2026, 9, 18, 15)])
def test_missing_or_naive_timestamp_never_becomes_fresh(alpaca, monkeypatch, timestamp):
    _, client, sdk = alpaca
    monkeypatch.setattr(
        sdk,
        "get_stock_latest_trade",
        lambda request: {"AAPL": SimpleNamespace(price=100, timestamp=timestamp)},
    )
    result = client.get_price_result("AAPL")
    assert result.value == 100
    assert result.as_of is None
    assert result.freshness is Freshness.STALE


def test_quote_age_is_configurable(alpaca, monkeypatch):
    _, client, sdk = alpaca
    stub_sdk(monkeypatch, sdk, NOW - timedelta(seconds=31))
    monkeypatch.setenv("ALPACA_MAX_QUOTE_AGE_SECONDS", "30")
    assert client.get_price_result("AAPL").freshness is Freshness.STALE
    monkeypatch.setenv("ALPACA_MAX_QUOTE_AGE_SECONDS", "60")
    assert client.get_price_result("AAPL").freshness is Freshness.FRESH


@pytest.mark.parametrize("route", ["builtin", "facade"])
def test_both_production_routes_preserve_old_trade_time(alpaca, monkeypatch, route):
    from benchmark.infrastructure.adapters.market import AlpacaMarketDataAdapter
    from services import market_data

    _, _, sdk = alpaca
    timestamp = NOW - timedelta(hours=1)
    stub_sdk(monkeypatch, sdk, timestamp)
    port = (
        AlpacaMarketDataAdapter()
        if route == "builtin"
        else market_data._create_market_data_port()
    )
    result = port.get_price("AAPL", Market.US)
    assert result.value == 100
    assert result.as_of == timestamp
    assert result.freshness is Freshness.STALE


@pytest.mark.parametrize("age", [121, -1])
def test_production_trading_rejects_old_and_future_iex_prices(alpaca, monkeypatch, age):
    from services import market_data
    from services.price_cache import PriceCache

    _, _, sdk = alpaca
    stub_sdk(monkeypatch, sdk, NOW - timedelta(seconds=age))
    monkeypatch.setattr(market_data, "price_cache", PriceCache())
    monkeypatch.setattr(
        market_data,
        "get_market_status_from_alpaca",
        lambda symbol: {"is_trading": True},
    )
    result = market_data.get_price_result("AAPL", "US", for_trading=True)
    assert result.freshness is Freshness.STALE
    with pytest.raises(RuntimeError, match="fresh trading price"):
        market_data.get_trading_price("AAPL", "US")


def test_market_closed_permits_old_valuation_but_rejects_trading(alpaca, monkeypatch):
    from benchmark.infrastructure.adapters.market import AlpacaMarketDataAdapter
    from benchmark.infrastructure.market import (
        DisplayMarketDataService,
        TradingMarketDataService,
    )

    module, _, sdk = alpaca
    timestamp = NOW - timedelta(days=1)
    stub_sdk(monkeypatch, sdk, timestamp)
    monkeypatch.setattr(
        module, "get_market_status_from_alpaca", lambda symbol: {"is_trading": False}
    )
    port = AlpacaMarketDataAdapter()
    display = DisplayMarketDataService(port).get_price("AAPL", Market.US)
    assert display.value == 100
    assert display.as_of == timestamp
    assert display.freshness is Freshness.STALE
    assert (
        TradingMarketDataService(port).require_price("AAPL", Market.US).freshness
        is Freshness.UNAVAILABLE
    )


def test_cached_iex_quote_expires_by_source_age_before_cache_ttl(alpaca, monkeypatch):
    from services import market_data, price_cache as cache_module
    from benchmark.infrastructure.cache import LegacyPriceCacheAdapter
    from benchmark.infrastructure.market import TradingMarketDataService

    _, _, sdk = alpaca
    source_time = NOW - timedelta(seconds=119)
    calls = stub_sdk(monkeypatch, sdk, source_time)
    clock = [NOW]
    monkeypatch.setattr(cache_module, "now_utc", lambda: clock[0])
    monkeypatch.setattr(cache_module, "now_timestamp", lambda: clock[0].timestamp())
    monkeypatch.setattr(alpaca[0], "now_utc", lambda: clock[0])
    monkeypatch.setattr(
        market_data,
        "get_market_status_from_alpaca",
        lambda symbol: {"is_trading": True},
    )
    cache = cache_module.PriceCache(ttl_seconds=30)
    service = TradingMarketDataService(
        market_data._create_market_data_port(), LegacyPriceCacheAdapter(cache)
    )
    assert service.require_price("AAPL", Market.US).freshness is Freshness.FRESH
    clock[0] += timedelta(seconds=2)
    cached = cache.get_result("AAPL", "US")
    assert cached.as_of == source_time
    assert cached.freshness is Freshness.STALE
    assert service.require_price("AAPL", Market.US).freshness is Freshness.STALE
    assert len(calls) == 2  # A stale cached result triggers a provider refresh.


def test_future_cached_iex_timestamp_is_rejected_but_crypto_is_unchanged(monkeypatch):
    from services import price_cache as module

    monkeypatch.setattr(module, "now_utc", lambda: NOW)
    monkeypatch.setattr(module, "now_timestamp", lambda: NOW.timestamp())
    cache = module.PriceCache()
    future = NOW + timedelta(seconds=1)
    cache.set_result(
        "AAPL",
        "US",
        PriceResult(Decimal("100"), future, "core.market.alpaca", Freshness.FRESH),
    )
    cache.set_result(
        "BTC",
        "CRYPTO",
        PriceResult(
            Decimal("100"),
            NOW - timedelta(days=1),
            "core.market.hyperliquid",
            Freshness.FRESH,
        ),
    )
    assert cache.get_result("AAPL", "US").freshness is Freshness.STALE
    assert cache.get_result("BTC", "CRYPTO").freshness is Freshness.FRESH
