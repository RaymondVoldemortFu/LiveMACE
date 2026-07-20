from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

import pytest

from benchmark.application.decisions import DecisionRoundService, RunDecisionRound
from benchmark.application.trading import TradeCommandGateway
from benchmark.contracts import Market, TradeCommand
from benchmark.infrastructure.cache import LegacyToolCacheAdapter
from benchmark.infrastructure.market.services import DisplayMarketDataService, TradingMarketDataService
from benchmark.infrastructure.market.symbols import infer_market, resolve_symbol_market
from benchmark.providers import Freshness, HealthStatus, KlineQuery, KlineResult, MarketDataPort, PriceResult
from benchmark.testing import FakeMarketDataPort as ReusableFakeMarketDataPort


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class FakeMarketDataPort(MarketDataPort):
    id = "fake.market"
    version = "1.0.0"
    capabilities = ("market.read",)
    config_schema: Mapping[str, object] = {"type": "object"}

    def __init__(self, price: PriceResult) -> None:
        self.price = price

    def get_price(self, symbol: str, market: Market) -> PriceResult:
        return self.price

    def get_klines(self, query: KlineQuery) -> KlineResult:
        return KlineResult(rows=({"symbol": query.symbol, "market": query.market.value},), source=self.id, freshness=Freshness.FRESH)

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


def test_m09_market_port_is_synchronous_and_returns_structured_results():
    port = FakeMarketDataPort(PriceResult(Decimal("100.5"), NOW, "fake", Freshness.FRESH))

    price = port.get_price("BTC", Market.CRYPTO)
    klines = port.get_klines(KlineQuery("BTC", Market.CRYPTO, "1m", 1))

    assert price.value == Decimal("100.5")
    assert price.freshness is Freshness.FRESH
    assert klines.rows[0]["market"] == "CRYPTO"
    assert port.healthcheck().status == "ok"


def test_m20_trading_market_service_rejects_non_fresh_or_non_positive_prices():
    stale_port = FakeMarketDataPort(PriceResult(Decimal("10"), NOW, "fake", Freshness.STALE))
    zero_port = FakeMarketDataPort(PriceResult(Decimal("0"), NOW, "fake", Freshness.FRESH))

    stale_result = TradingMarketDataService(stale_port).require_price("BTC", Market.CRYPTO)
    zero_result = TradingMarketDataService(zero_port).require_price("BTC", Market.CRYPTO)

    assert stale_result.freshness is Freshness.STALE
    assert stale_result.error == "fresh price is required for trading"
    assert zero_result.freshness is Freshness.UNAVAILABLE
    assert zero_result.error == "price is unavailable or non-positive"


def test_m20_display_market_service_can_allow_or_reject_stale_prices():
    port = FakeMarketDataPort(PriceResult(Decimal("10"), NOW, "fake", Freshness.STALE))
    display = DisplayMarketDataService(port)

    assert display.get_price("BTC", Market.CRYPTO, allow_stale=True).freshness is Freshness.STALE
    rejected = display.get_price("BTC", Market.CRYPTO, allow_stale=False)
    assert rejected.freshness is Freshness.UNAVAILABLE
    assert rejected.error == "stale price is not allowed"


def test_m11_gateway_is_synchronous_and_idempotent():
    calls = []

    def fake_legacy(**kwargs):
        calls.append(kwargs)
        return {"executed": True, "order_id": 123, "trade_id": 456}


    gateway = TradeCommandGateway(db=object(), executor=fake_legacy)
    command = TradeCommand(
        account_id=1,
        operation="open",
        market=Market.CRYPTO,
        symbol="btc",
        direction="long",
        sizing_mode="portion",
        sizing_value=Decimal("0.2"),
        leverage=1,
        reason="test",
        idempotency_key="round-1:call-1",
    )

    first = gateway.execute(command)
    second = gateway.execute(command)

    assert first.accepted is True
    assert first.executed is True
    assert first.order_id == 123
    assert second is first
    assert len(calls) == 1
    assert first.normalized_command.symbol == "BTC"


def test_m11_gateway_maps_rejects_to_stable_codes():
    def fake_legacy(**kwargs):
        return {"executed": False, "error": "US market is closed for AAPL"}


    gateway = TradeCommandGateway(db=object(), executor=fake_legacy)
    command = TradeCommand(1, "open", Market.US, "AAPL", "long", "portion", Decimal("0.1"), 1, "test", "k")

    result = gateway.execute(command)

    assert result.accepted is False
    assert result.executed is False
    assert result.reject_code == "MARKET_CLOSED"
    assert result.normalized_command.market is Market.US


def test_m10_run_decision_round_validates_sync_request_shape():
    called = []

    def fake_entrypoint():
        called.append("ran")


    result = DecisionRoundService(entrypoint=fake_entrypoint).run(RunDecisionRound(account_ids=None, max_concurrency=2, trigger="test"))

    assert called == ["ran"]
    assert result.processed_accounts == 0
    with pytest.raises(ValueError, match="max_concurrency"):
        RunDecisionRound(account_ids=None, max_concurrency=0, trigger="test")
    with pytest.raises(ValueError, match="account_ids"):
        RunDecisionRound(account_ids=(0,), max_concurrency=1, trigger="test")

class FakeRedisToolCache:
    def __init__(self):
        self.values = {}

    def get_json(self, namespace, args, round_id=None, suppress_miss_log=False):
        return self.values.get((namespace, tuple(sorted(args.items())), round_id))

    def set_json(self, namespace, args, value, ttl_seconds=None, round_id=None):
        self.values[(namespace, tuple(sorted(args.items())), round_id)] = value

    @contextmanager
    def acquire_lock(self, namespace, args, round_id=None):
        yield True


def test_m09_reusable_fake_port_and_m20_tool_cache_adapter_contract():
    fake_market = ReusableFakeMarketDataPort(Decimal("42"), Freshness.FRESH)
    assert fake_market.get_price("BTC", Market.CRYPTO).value == Decimal("42")

    cache = LegacyToolCacheAdapter(FakeRedisToolCache())
    args = {"symbol": "BTC", "market": "CRYPTO"}
    assert cache.get("price", args, round_id="round-1") is None
    cache.set("price", args, {"value": 42}, round_id="round-1")
    assert cache.get("price", args, round_id="round-1") == {"value": 42}
    with cache.acquire_lock("price", args, round_id="round-1") as locked:
        assert locked is True


def test_m20_symbol_registry_normalizes_and_validates_supported_markets():
    assert infer_market("AAPL") is Market.US
    resolved = resolve_symbol_market("btc", "crypto")
    assert resolved.symbol == "BTC"
    assert resolved.market is Market.CRYPTO
    with pytest.raises(ValueError, match="Unsupported US stock"):
        resolve_symbol_market("BTC", "US")
