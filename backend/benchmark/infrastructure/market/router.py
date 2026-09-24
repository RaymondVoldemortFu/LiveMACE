"""MarketDataPort router for the built-in market providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from benchmark.contracts import JsonValue, Market
from benchmark.providers import (
    HealthStatus,
    KlineQuery,
    KlineResult,
    MarketDataPort,
    MarketStatusResult,
    PriceResult,
)


@dataclass(frozen=True)
class RoutedMarketDataPort:
    providers: Mapping[Market, MarketDataPort]

    id: ClassVar[str] = "core.market.router"
    version: ClassVar[str] = "1.0.0"
    capabilities: ClassVar[tuple[str, ...]] = ("market.read",)
    config_schema: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "additionalProperties": False,
    }

    def _provider(self, market: Market) -> MarketDataPort:
        try:
            return self.providers[market]
        except KeyError as exc:
            raise ValueError(f"No market provider registered for {market.value}") from exc

    def provider_descriptor(self, market: Market) -> tuple[str, str]:
        provider = self._provider(market)
        return provider.id, provider.version

    def get_price(self, symbol: str, market: Market) -> PriceResult:
        return self._provider(market).get_price(symbol, market)

    def get_klines(self, query: KlineQuery) -> KlineResult:
        return self._provider(query.market).get_klines(query)

    def get_market_status(self, symbol: str, market: Market) -> MarketStatusResult:
        return self._provider(market).get_market_status(symbol, market)

    def healthcheck(self) -> HealthStatus:
        statuses = []
        for provider in self.providers.values():
            try:
                statuses.append(provider.healthcheck())
            except (KeyboardInterrupt, SystemExit, GeneratorExit):
                raise
            except Exception as exc:
                statuses.append(
                    HealthStatus(
                        status="unavailable",
                        provider_id=provider.id,
                        message=str(exc) or "market provider healthcheck failed",
                    )
                )
        if any(status.status == "unavailable" for status in statuses):
            return HealthStatus(
                status="unavailable",
                provider_id=self.id,
                message="one or more market providers are unavailable",
            )
        if any(status.status == "degraded" for status in statuses):
            return HealthStatus(
                status="degraded",
                provider_id=self.id,
                message="one or more market providers are degraded",
            )
        return HealthStatus(status="ok", provider_id=self.id)


__all__ = ["RoutedMarketDataPort"]
