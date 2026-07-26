from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

import pytest

from benchmark.application.decisions import DecisionRoundService, RunDecisionRound
from benchmark.application.trading import TradeCommandGateway, TradeCommandIdempotencyStore
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


def test_m11_gateway_caches_rejected_results_for_same_key():
    calls = []

    def fake_legacy(**kwargs):
        calls.append(kwargs)
        return {"executed": False, "error": "market is closed"}

    store = TradeCommandIdempotencyStore()
    gateway = TradeCommandGateway(db=object(), executor=fake_legacy, _store=store)
    command = TradeCommand(1, "open", Market.CRYPTO, "BTC", "long", "portion", Decimal("0.2"), 1, "test", "round-1:call-1")

    first = gateway.execute(command)
    second = gateway.execute(command)

    assert first.accepted is False
    assert second.accepted is False
    assert second is first
    assert len(calls) == 1



def test_m11_gateway_deduplicates_concurrent_same_key():
    started = Event()
    release = Event()
    calls = []

    def fake_legacy(**kwargs):
        calls.append(kwargs)
        started.set()
        release.wait(timeout=2)
        return {"executed": True, "order_id": 123, "trade_id": 456}

    store = TradeCommandIdempotencyStore()
    gateway = TradeCommandGateway(db=object(), executor=fake_legacy, _store=store)
    command = TradeCommand(1, "open", Market.CRYPTO, "BTC", "long", "portion", Decimal("0.2"), 1, "test", "same-key")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(gateway.execute, command)
        assert started.wait(timeout=2)
        second_future = pool.submit(gateway.execute, command)
        release.set()
        first = first_future.result(timeout=2)
        second = second_future.result(timeout=2)

    assert first is second
    assert len(calls) == 1


def test_m11_default_gateway_reuses_idempotency_store_between_instances(monkeypatch):
    import benchmark.application.trading.gateway as gateway_module

    calls = []

    def fake_legacy(**kwargs):
        calls.append(kwargs)
        return {"executed": True, "order_id": 321}

    first_gateway = gateway_module.get_default_trade_gateway(object())
    second_gateway = gateway_module.get_default_trade_gateway(object())
    first_gateway.executor = fake_legacy
    second_gateway.executor = fake_legacy
    command = TradeCommand(1, "open", Market.CRYPTO, "BTC", "long", "portion", Decimal("0.2"), 1, "test", "shared-key")

    first = first_gateway.execute(command)
    second = second_gateway.execute(command)

    assert first is second
    assert len(calls) == 1

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


def test_m11_execute_trade_tool_uses_round_tool_call_idempotency_key(monkeypatch):
    from benchmark.contracts import TradeCommandResult

    monkeypatch.setenv("ALPACA_KEY", "dummy")
    monkeypatch.setenv("ALPACA_SECRET", "dummy")

    import benchmark.application.trading as trading_app
    from services.agent import trade_execution_tool

    captured_keys = []

    class FakeGateway:
        def execute(self, command):
            captured_keys.append(command.idempotency_key)
            return TradeCommandResult(
                True,
                True,
                None,
                None,
                1,
                2,
                command,
            )

    monkeypatch.setattr(trading_app, "get_default_trade_gateway", lambda db: FakeGateway())

    trade_execution_tool.execute_trade_tool(
        db=object(),
        account_id=1,
        operation=" OPEN ",
        symbol=" btc ",
        market="crypto",
        direction=" LONG ",
        target_portion_of_balance=0.1,
        leverage="2",
        decision_round_id="round-1",
        tool_call_id="call-1",
    )
    trade_execution_tool.execute_trade_tool(
        db=object(),
        account_id=1,
        operation="open",
        symbol="BTC",
        market="CRYPTO",
        direction="long",
        target_portion_of_balance=0.1,
        leverage=2,
        decision_round_id="round-2",
        tool_call_id="call-1",
    )

    assert captured_keys == [
        "round-1:call-1",
        "round-2:call-1",
    ]


def test_m11_execute_trade_tool_without_round_uses_per_call_key(monkeypatch):
    from benchmark.contracts import TradeCommandResult

    monkeypatch.setenv("ALPACA_KEY", "dummy")
    monkeypatch.setenv("ALPACA_SECRET", "dummy")

    import benchmark.application.trading as trading_app
    from services.agent import trade_execution_tool

    captured_keys = []

    class FakeGateway:
        def execute(self, command):
            captured_keys.append(command.idempotency_key)
            return TradeCommandResult(True, True, None, None, 1, 2, command)

    monkeypatch.setattr(trading_app, "get_default_trade_gateway", lambda db: FakeGateway())

    for _ in range(2):
        trade_execution_tool.execute_trade_tool(
            db=object(),
            account_id=1,
            operation="open",
            symbol="BTC",
            market="CRYPTO",
            direction="long",
            target_portion_of_balance=0.1,
            leverage=2,
        )

    assert captured_keys[0].startswith("tool:")
    assert captured_keys[1].startswith("tool:")
    assert captured_keys[0] != captured_keys[1]





def test_m09_sandbox_adapter_uses_lease_container():
    from benchmark.infrastructure.adapters.sandbox import ContainerServiceSandboxAdapter

    class FakeContainerService:
        def __init__(self):
            self.leased = []
            self.released = []

        def lease_container(self, account_id):
            self.leased.append(account_id)
            return "container-1"

        def release_container(self, account_id):
            self.released.append(account_id)

    service = FakeContainerService()
    adapter = ContainerServiceSandboxAdapter(service)

    lease = adapter.lease(7)
    adapter.release(lease)

    assert lease.container_id == "container-1"
    assert service.leased == [7]
    assert service.released == [7]


def test_m09_sandbox_adapter_rejects_missing_container_id():
    from benchmark.infrastructure.adapters.sandbox import ContainerServiceSandboxAdapter
    from benchmark.providers.errors import ProviderError

    class FakeContainerService:
        def lease_container(self, account_id):
            return None

    with pytest.raises(ProviderError, match="failed to lease sandbox container"):
        ContainerServiceSandboxAdapter(FakeContainerService()).lease(7)


def test_m11_execute_trade_tool_preserves_legacy_result_fields(monkeypatch):
    from benchmark.contracts import TradeCommandResult

    monkeypatch.setenv("ALPACA_KEY", "dummy")
    monkeypatch.setenv("ALPACA_SECRET", "dummy")

    import benchmark.application.trading as trading_app
    from services.agent import trade_execution_tool

    class FakeGateway:
        def execute(self, command):
            return TradeCommandResult(
                True,
                True,
                None,
                None,
                1,
                2,
                command,
                {
                    "executed": True,
                    "operation": "close_all",
                    "closed_orders": [],
                    "message": "No positions to close.",
                    "quantity": 0,
                    "notional_usd": 0,
                    "order_no": "ORD-1",
                    "size_mode": "close_all",
                },
            )

    monkeypatch.setattr(trading_app, "get_default_trade_gateway", lambda db: FakeGateway())

    result = trade_execution_tool.execute_trade_tool(db=object(), account_id=1, operation="close_all")

    assert result["closed_orders"] == []
    assert result["message"] == "No positions to close."
    assert result["quantity"] == 0
    assert result["notional_usd"] == 0
    assert result["order_no"] == "ORD-1"
    assert result["size_mode"] == "close_all"


def test_m09_legacy_memory_adapter_matches_existing_store_contract():
    from datetime import datetime, timezone

    from benchmark.contracts import Market
    from benchmark.infrastructure.adapters.memory import LegacyMemoryStoreAdapter

    db = object()

    class FakeLegacyMemory:
        def __init__(self):
            self.add_calls = []
            self.search_calls = []
            self.clear_calls = []

        def add(self, content, account_id, metadata=None, trace_id=None, db=None, market="CRYPTO"):
            self.add_calls.append(
                {
                    "content": content,
                    "account_id": account_id,
                    "metadata": metadata,
                    "trace_id": trace_id,
                    "db": db,
                    "market": market,
                }
            )
            return {"memory_id": "mem-42"}

        def search(self, query, account_id, limit=2, db=None, market="CRYPTO"):
            self.search_calls.append(
                {
                    "query": query,
                    "account_id": account_id,
                    "limit": limit,
                    "db": db,
                    "market": market,
                }
            )
            return [
                {
                    "id": 5,
                    "content": "US memory",
                    "metadata": {"source": "test"},
                    "similarity": "0.75",
                    "created_at": "2026-07-20T12:00:00",
                }
            ]

        def clear_account_memories(self, account_id):
            self.clear_calls.append(account_id)
            return 3

    legacy = FakeLegacyMemory()
    adapter = LegacyMemoryStoreAdapter(legacy)

    memory_id = adapter.add(7, "remember AAPL", {"symbol": "AAPL"}, market=Market.US, trace_id="trace-1", db=db)
    records = adapter.search(7, "AAPL", 4, market=Market.US, db=db)
    deleted = adapter.delete_all(7)

    assert memory_id == "mem-42"
    assert legacy.add_calls == [
        {
            "content": "remember AAPL",
            "account_id": "7",
            "metadata": {"symbol": "AAPL"},
            "trace_id": "trace-1",
            "db": db,
            "market": "US",
        }
    ]
    assert legacy.search_calls == [{"query": "AAPL", "account_id": "7", "limit": 4, "db": db, "market": "US"}]
    assert legacy.clear_calls == ["7"]
    assert deleted == 3
    assert records[0].id == "5"
    assert records[0].content == "US memory"
    assert records[0].metadata["source"] == "test"
    assert records[0].score == 0.75
    expected_created_at = datetime(2026, 7, 20, 12, 0).astimezone().astimezone(timezone.utc)
    assert records[0].created_at == expected_created_at
