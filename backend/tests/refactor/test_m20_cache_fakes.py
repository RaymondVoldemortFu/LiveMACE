from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from benchmark.contracts import Market
from benchmark.providers import Freshness, KlineQuery, PriceResult
from benchmark.testing import FakeKlineCache, FakePriceCache, FakeToolCache


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def test_fake_price_cache_set_get_clear_lifecycle():
    cache = FakePriceCache()
    result = PriceResult(Decimal("42"), NOW, "fake.provider", Freshness.FRESH)

    assert cache.get("BTC", Market.CRYPTO) is None
    cache.set("btc", Market.CRYPTO, result)
    assert cache.get("BTC", Market.CRYPTO) == result
    assert cache.get("BTC", Market.US) is None

    cache.clear()
    assert cache.get("BTC", Market.CRYPTO) is None


def test_fake_kline_cache_isolates_query_lifecycles():
    cache = FakeKlineCache()
    query = KlineQuery(
        "BTC",
        Market.CRYPTO,
        "1m",
        20,
        start_time={"timestamp": 1},
        end_time=2,
    )
    other_query = KlineQuery("BTC", Market.CRYPTO, "1m", 21)
    rows = ({"timestamp": 1, "close": 42},)

    assert cache.get(query) == ()
    cache.set(query, rows)
    assert cache.get(query) == rows
    assert cache.get(other_query) == ()
    assert FakeKlineCache().get(query) == ()


def test_fake_tool_cache_isolates_rounds_and_releases_locks():
    cache = FakeToolCache()
    args = {"symbol": "BTC", "market": "CRYPTO"}
    value = {"price": 42}

    assert cache.get("price", args, round_id="round-1") is None
    cache.set("price", args, value, round_id="round-1")
    assert cache.get("price", args, round_id="round-1") == value
    assert cache.get("price", args, round_id="round-2") is None

    with cache.acquire_lock("price", args, round_id="round-1") as first:
        with cache.acquire_lock("price", args, round_id="round-1") as second:
            assert first is True
            assert second is False

    with cache.acquire_lock("price", args, round_id="round-1") as reacquired:
        assert reacquired is True
