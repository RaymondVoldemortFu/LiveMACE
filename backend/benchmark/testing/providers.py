"""Deterministic fake provider ports for contract and extension tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping
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

    def __init__(self, responses: tuple[LLMResponse, ...] = ()) -> None:
        self._responses = list(responses)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeLLMClientPort has no queued response")
        return self._responses.pop(0)

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


class FakeMemoryStorePort:
    id = "fake.memory"
    version = "1.0.0"
    capabilities = ("memory.read", "memory.write")
    config_schema = _EMPTY_SCHEMA

    def __init__(self) -> None:
        self._records: dict[tuple[str, Market], list[MemoryRecord]] = {}

    def search(
        self,
        account_id: int | str,
        query: str,
        limit: int,
        *,
        market: Market,
    ) -> tuple[MemoryRecord, ...]:
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
    ) -> None:
        self._price = price
        self._freshness = freshness
        self._is_trading = is_trading

    def get_price(self, symbol: str, market: Market) -> PriceResult:
        if self._price is None:
            return PriceResult(
                None,
                None,
                self.id,
                Freshness.UNAVAILABLE,
                "fake price unavailable",
            )
        return PriceResult(
            self._price,
            datetime.now(timezone.utc),
            self.id,
            self._freshness,
        )

    def get_klines(self, query: KlineQuery) -> KlineResult:
        return KlineResult(
            ({"symbol": query.symbol, "market": query.market.value},),
            self.id,
            Freshness.FRESH,
        )

    def get_market_status(
        self,
        symbol: str,
        market: Market,
    ) -> MarketStatusResult:
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

    def lease(self, account_id: int) -> SandboxLease:
        if account_id in self.active:
            raise AssertionError(f"account {account_id} already has an active lease")
        lease = SandboxLease(account_id, f"fake-container-{account_id}")
        self.active[account_id] = lease
        return lease

    def release(self, lease: SandboxLease) -> None:
        self.active.pop(lease.account_id, None)

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


__all__ = [
    "FakeLLMClientPort",
    "FakeMarketDataPort",
    "FakeMemoryStorePort",
    "FakeSandboxPort",
]
