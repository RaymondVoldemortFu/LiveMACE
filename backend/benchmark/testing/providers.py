"""Deterministic fake provider ports for contract and extension tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from benchmark.contracts import JsonValue, Market
from benchmark.providers import (
    Freshness,
    HealthStatus,
    KlineQuery,
    KlineResult,
    LLMRequest,
    LLMResponse,
    MarketStatusResult,
    MemoryRecord,
    PriceResult,
    SandboxLease,
)

_EMPTY_SCHEMA: Mapping[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
}


class FakeLLMClientPort:
    id = "fake.llm"
    version = "1.0.0"
    capabilities = ("llm.complete", "llm.tools")
    config_schema = _EMPTY_SCHEMA

    def __init__(
        self,
        responses: Iterable[LLMResponse] = (),
        *,
        default_response: LLMResponse | None = None,
    ) -> None:
        self._responses = list(responses)
        if not all(isinstance(item, LLMResponse) for item in self._responses):
            raise TypeError("responses must contain LLMResponse values")
        if default_response is not None and not isinstance(
            default_response, LLMResponse
        ):
            raise TypeError("default_response must be LLMResponse or None")
        self.default_response = default_response
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not isinstance(request, LLMRequest):
            raise TypeError("request must be LLMRequest")
        self.requests.append(request)
        if self._responses:
            return self._responses.pop(0)
        if self.default_response is not None:
            return self.default_response
        raise AssertionError("FakeLLMClientPort has no queued response")

    @property
    def last_request(self) -> LLMRequest | None:
        return self.requests[-1] if self.requests else None

    def queue(self, *responses: LLMResponse) -> None:
        if not all(isinstance(item, LLMResponse) for item in responses):
            raise TypeError("responses must be LLMResponse values")
        self._responses.extend(responses)

    def reset(self) -> None:
        self._responses.clear()
        self.requests.clear()

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


class FakeMemoryStorePort:
    id = "fake.memory"
    version = "1.0.0"
    capabilities = ("memory.read", "memory.write")
    config_schema = _EMPTY_SCHEMA

    def __init__(self) -> None:
        self._records: dict[tuple[str, Market], list[MemoryRecord]] = {}
        self.search_calls: list[dict[str, object]] = []
        self.add_calls: list[dict[str, object]] = []

    def search(
        self,
        account_id: int | str,
        query: str,
        limit: int,
        *,
        market: Market,
    ) -> tuple[MemoryRecord, ...]:
        _require_market(market)
        self.search_calls.append(
            {
                "account_id": account_id,
                "query": query,
                "limit": limit,
                "market": market,
            }
        )
        records = self._records.get((str(account_id), market), [])
        matching = [item for item in records if query.lower() in item.content.lower()]
        return tuple(matching[:limit])

    def add(
        self,
        account_id: int | str,
        content: str,
        metadata: Mapping[str, JsonValue],
        *,
        market: Market,
    ) -> str:
        _require_market(market)
        self.add_calls.append(
            {
                "account_id": account_id,
                "content": content,
                "metadata": metadata,
                "market": market,
            }
        )
        record_id = f"memory-{uuid4().hex}"
        record = MemoryRecord(
            id=record_id,
            content=content,
            metadata=metadata,
            created_at=datetime.now(timezone.utc),
        )
        self._records.setdefault((str(account_id), market), []).append(record)
        return record_id

    def delete_all(self, account_id: int | str) -> int:
        account_key = str(account_id)
        keys = [key for key in self._records if key[0] == account_key]
        count = sum(len(self._records[key]) for key in keys)
        for key in keys:
            del self._records[key]
        return count

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


def _require_market(market: Market) -> None:
    if not isinstance(market, Market):
        raise TypeError("market must be Market")


class FakeMarketDataPort:
    id = "fake.market"
    version = "1.0.0"
    capabilities = ("market.read",)
    config_schema = _EMPTY_SCHEMA

    def __init__(
        self,
        price: Decimal | None = Decimal("100"),
        freshness: Freshness = Freshness.FRESH,
        *,
        is_trading: bool = True,
        prices: Mapping[str, Decimal] | None = None,
        klines: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        if price is not None and not isinstance(price, Decimal):
            price = Decimal(str(price))
        if price is not None and not price.is_finite():
            raise ValueError("price must be finite or None")
        self._price = price
        self._freshness = freshness
        self._is_trading = is_trading
        normalized_prices: dict[str, Decimal] = {}
        for symbol, value in (prices or {}).items():
            try:
                normalized = (
                    value if isinstance(value, Decimal) else Decimal(str(value))
                )
            except Exception as exc:
                raise TypeError(
                    "prices must contain Decimal-compatible values"
                ) from exc
            if not normalized.is_finite():
                raise ValueError("prices must contain finite Decimal values")
            normalized_prices[str(symbol).strip().upper()] = normalized
        self._prices = normalized_prices
        self._klines = tuple(dict(row) for row in (klines or ()))
        self.price_calls: list[tuple[str, Market]] = []
        self.kline_calls: list[KlineQuery] = []
        self.status_calls: list[tuple[str, Market]] = []

    def get_price(self, symbol: str, market: Market) -> PriceResult:
        if not isinstance(market, Market):
            raise TypeError("market must be Market")
        self.price_calls.append((symbol, market))
        selected = self._prices.get(str(symbol).strip().upper(), self._price)
        if selected is None:
            return PriceResult(
                None,
                None,
                self.id,
                Freshness.UNAVAILABLE,
                "fake price unavailable",
            )
        return PriceResult(
            selected,
            datetime.now(timezone.utc),
            self.id,
            self._freshness,
        )

    def get_klines(self, query: KlineQuery) -> KlineResult:
        if not isinstance(query, KlineQuery):
            raise TypeError("query must be KlineQuery")
        self.kline_calls.append(query)
        return KlineResult(
            self._klines or ({"symbol": query.symbol, "market": query.market.value},),
            self.id,
            Freshness.FRESH,
        )

    def get_market_status(
        self,
        symbol: str,
        market: Market,
    ) -> MarketStatusResult:
        if not isinstance(market, Market):
            raise TypeError("market must be Market")
        self.status_calls.append((symbol, market))
        return MarketStatusResult(
            self._is_trading,
            self.id,
            datetime.now(timezone.utc),
            None if self._is_trading else "fake market closed",
        )

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


class FakeSandboxPort:
    id = "fake.sandbox"
    version = "1.0.0"
    capabilities = ("sandbox.read", "sandbox.write")
    config_schema = _EMPTY_SCHEMA

    def __init__(self) -> None:
        self.active: dict[int, SandboxLease] = {}
        self.lease_calls: list[int] = []
        self.release_calls: list[SandboxLease] = []

    def lease(self, account_id: int) -> SandboxLease:
        if (
            not isinstance(account_id, int)
            or isinstance(account_id, bool)
            or account_id <= 0
        ):
            raise ValueError("account_id must be a positive integer")
        self.lease_calls.append(account_id)
        if account_id in self.active:
            raise AssertionError(f"account {account_id} already has an active lease")
        lease = SandboxLease(account_id, f"fake-container-{account_id}")
        self.active[account_id] = lease
        return lease

    def release(self, lease: SandboxLease) -> None:
        if not isinstance(lease, SandboxLease):
            raise TypeError("lease must be SandboxLease")
        self.release_calls.append(lease)
        self.active.pop(lease.account_id, None)

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


__all__ = [
    "FakeLLMClientPort",
    "FakeMarketDataPort",
    "FakeMemoryStorePort",
    "FakeSandboxPort",
]

# Short aliases are convenient in small extension test modules while the
# explicit Port names remain the canonical API.
FakeLLM = FakeLLMClientPort
FakeMarket = FakeMarketDataPort
FakeMarketData = FakeMarketDataPort
FakeMemory = FakeMemoryStorePort
FakeMemoryStore = FakeMemoryStorePort
FakeSandbox = FakeSandboxPort

__all__ += [
    "FakeLLM",
    "FakeMarket",
    "FakeMarketData",
    "FakeMemory",
    "FakeMemoryStore",
    "FakeSandbox",
]
