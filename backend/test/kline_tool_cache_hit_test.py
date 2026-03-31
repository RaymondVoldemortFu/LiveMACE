import sys
from pathlib import Path

import fakeredis


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import market_data
from services.tool_cache import tool_cache


def _use_fake_redis():
    fake_client = fakeredis.FakeRedis(decode_responses=True)
    original_client = tool_cache._client
    tool_cache._client = fake_client
    return fake_client, original_client


def test_tool_cache_key_and_round_index_behavior():
    fake_client, original_client = _use_fake_redis()
    round_id = tool_cache.create_round_id(scope="pytest")
    args = {"symbol": "BTC", "period": "1m", "count": 20}
    payload = [{"timestamp": 1, "close": 100.0}]

    try:
        with tool_cache.use_round(round_id):
            ok = tool_cache.set_json("get_kline_data", args, payload, ttl_seconds=120)
            assert ok is True
            key = tool_cache._build_data_key("get_kline_data", args, round_id)
            index_key = tool_cache._build_round_index_key(round_id)

            assert fake_client.exists(key) == 1
            assert key in fake_client.smembers(index_key)
            cached = tool_cache.get_json("get_kline_data", args)
            assert cached == payload
    finally:
        tool_cache.clear_round(round_id)
        tool_cache._client = original_client


def test_tool_cache_args_equivalence_same_semantics_hits():
    _, original_client = _use_fake_redis()
    round_id = tool_cache.create_round_id(scope="pytest")
    args_a = {"symbol": "BTC", "period": "1m", "count": 20}
    # Same semantic content, different insertion order.
    args_b = {"count": 20, "period": "1m", "symbol": "BTC"}
    payload = [{"timestamp": 1, "close": 101.0}]

    try:
        with tool_cache.use_round(round_id):
            assert tool_cache.set_json("get_kline_data", args_a, payload, ttl_seconds=120) is True
            assert tool_cache.get_json("get_kline_data", args_b) == payload
    finally:
        tool_cache.clear_round(round_id)
        tool_cache._client = original_client


def test_tool_cache_different_tool_types_and_args_are_isolated():
    _, original_client = _use_fake_redis()
    round_id = tool_cache.create_round_id(scope="pytest")
    args_common = {"url": "https://example.com"}
    args_other = {"url": "https://example.com", "mode": "full"}

    try:
        with tool_cache.use_round(round_id):
            assert tool_cache.set_json("extract_tool", args_common, {"a": 1}, ttl_seconds=120) is True
            assert tool_cache.set_json("search_tool", args_common, {"b": 2}, ttl_seconds=120) is True
            assert tool_cache.set_json("extract_tool", args_other, {"c": 3}, ttl_seconds=120) is True

            assert tool_cache.get_json("extract_tool", args_common) == {"a": 1}
            assert tool_cache.get_json("search_tool", args_common) == {"b": 2}
            assert tool_cache.get_json("extract_tool", args_other) == {"c": 3}
            assert tool_cache.get_json("search_tool", args_other) is None
    finally:
        tool_cache.clear_round(round_id)
        tool_cache._client = original_client


def test_kline_cache_hit_in_same_decision_round(monkeypatch):
    _, original_client = _use_fake_redis()
    calls = {"count": 0}

    def _fake_fetch(symbol, period, count, start_time, end_time):
        calls["count"] += 1
        return [{"timestamp": 1, "close": 101.23}]

    monkeypatch.setattr(market_data, "_save_klines", lambda *args, **kwargs: None)
    monkeypatch.setattr(market_data, "get_kline_data_from_hyperliquid", _fake_fetch)

    round_id = tool_cache.create_round_id(scope="pytest")
    try:
        with tool_cache.use_round(round_id):
            first = market_data.get_kline_data(
                "BTC", "CRYPTO", period="1m", count=20, start_time=1, end_time=2
            )
            second = market_data.get_kline_data(
                "BTC", "CRYPTO", period="1m", count=20, start_time=1, end_time=2
            )

        assert first == second
        assert calls["count"] == 1
    finally:
        tool_cache.clear_round(round_id)
        tool_cache._client = original_client


def test_kline_cache_hit_with_symbol_market_normalization(monkeypatch):
    _, original_client = _use_fake_redis()
    calls = {"count": 0}

    def _fake_fetch(symbol, period, count, start_time, end_time):
        calls["count"] += 1
        return [{"timestamp": 1, "close": 109.99}]

    monkeypatch.setattr(market_data, "_save_klines", lambda *args, **kwargs: None)
    monkeypatch.setattr(market_data, "get_kline_data_from_hyperliquid", _fake_fetch)

    round_id = tool_cache.create_round_id(scope="pytest")
    try:
        with tool_cache.use_round(round_id):
            first = market_data.get_kline_data(
                "btc", "crypto", period="1m", count=30, start_time=10, end_time=20
            )
            second = market_data.get_kline_data(
                "BTC", "CRYPTO", period="1m", count=30, start_time=10, end_time=20
            )

        assert first == second
        assert calls["count"] == 1
    finally:
        tool_cache.clear_round(round_id)
        tool_cache._client = original_client


def test_kline_cache_miss_for_different_parameter_class(monkeypatch):
    _, original_client = _use_fake_redis()
    calls = {"count": 0}

    def _fake_fetch(symbol, period, count, start_time, end_time):
        calls["count"] += 1
        return [{"timestamp": calls["count"], "close": 88.88}]

    monkeypatch.setattr(market_data, "_save_klines", lambda *args, **kwargs: None)
    monkeypatch.setattr(market_data, "get_kline_data_from_hyperliquid", _fake_fetch)

    round_id = tool_cache.create_round_id(scope="pytest")
    try:
        with tool_cache.use_round(round_id):
            market_data.get_kline_data("BTC", "CRYPTO", period="1m", count=20, start_time=1, end_time=2)
            market_data.get_kline_data("BTC", "CRYPTO", period="1m", count=21, start_time=1, end_time=2)

        # count changed from 20 -> 21, should miss and fetch again
        assert calls["count"] == 2
    finally:
        tool_cache.clear_round(round_id)
        tool_cache._client = original_client


def test_kline_cache_isolated_across_different_rounds(monkeypatch):
    _, original_client = _use_fake_redis()
    calls = {"count": 0}

    def _fake_fetch(symbol, period, count, start_time, end_time):
        calls["count"] += 1
        return [{"timestamp": calls["count"], "close": 77.77}]

    monkeypatch.setattr(market_data, "_save_klines", lambda *args, **kwargs: None)
    monkeypatch.setattr(market_data, "get_kline_data_from_hyperliquid", _fake_fetch)

    round_a = tool_cache.create_round_id(scope="pytest")
    round_b = tool_cache.create_round_id(scope="pytest")
    try:
        with tool_cache.use_round(round_a):
            market_data.get_kline_data(
                "BTC", "CRYPTO", period="1m", count=20, start_time=1, end_time=2
            )

        with tool_cache.use_round(round_b):
            market_data.get_kline_data(
                "BTC", "CRYPTO", period="1m", count=20, start_time=1, end_time=2
            )

        # Same request but different rounds should not share cache.
        assert calls["count"] == 2
    finally:
        tool_cache.clear_round(round_a)
        tool_cache.clear_round(round_b)
        tool_cache._client = original_client


def test_kline_cache_hit_when_times_differ_within_same_period_bucket(monkeypatch):
    _, original_client = _use_fake_redis()
    calls = {"count": 0}

    def _fake_fetch(symbol, period, count, start_time, end_time):
        calls["count"] += 1
        return [{"timestamp": calls["count"], "close": 66.66}]

    monkeypatch.setattr(market_data, "_save_klines", lambda *args, **kwargs: None)
    monkeypatch.setattr(market_data, "get_kline_data_from_hyperliquid", _fake_fetch)

    round_id = tool_cache.create_round_id(scope="pytest")
    try:
        with tool_cache.use_round(round_id):
            # 1d period: both end_time values are on same day, should normalize to same cache bucket.
            market_data.get_kline_data(
                "BTC", "CRYPTO", period="1d", count=20, start_time=1772323200123, end_time=1772409599000
            )
            market_data.get_kline_data(
                "BTC", "CRYPTO", period="1d", count=20, start_time=1772323200999, end_time=1772409599999
            )

        assert calls["count"] == 1
    finally:
        tool_cache.clear_round(round_id)
        tool_cache._client = original_client

