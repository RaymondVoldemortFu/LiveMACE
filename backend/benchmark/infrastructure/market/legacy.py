"""Adapters around the existing market data implementation."""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from benchmark.contracts import JsonValue, Market
from benchmark.providers import (
    Freshness,
    HealthStatus,
    KlineQuery,
    KlineResult,
    MarketDataPort,
    MarketStatusResult,
    PriceResult,
    ProviderError,
)
from benchmark.providers.runtime import (
    provider_failure,
    require_sync_result,
    run_health_probe,
)


class LegacyMarketDataAdapter(MarketDataPort):
    """Thin synchronous adapter preserving services.market_data behavior."""

    id = "core.market.legacy"
    version = "1.0.0"
    capabilities = ("market.read",)
    config_schema: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": False}

    def get_price(self, symbol: str, market: Market) -> PriceResult:
        try:
            from services import market_data
            from services.time_source import now_utc

            value = Decimal(
                str(
                    require_sync_result(
                        market_data.get_last_price(symbol, market.value),
                        provider_id=self.id,
                        operation="get_price",
                    )
                )
            )
            if not value.is_finite() or value <= 0:
                return PriceResult(
                    value=None,
                    as_of=now_utc(),
                    source=self.id,
                    freshness=Freshness.UNAVAILABLE,
                    error="price is not positive or finite",
                )
            return PriceResult(
                value=value,
                as_of=now_utc(),
                source=self.id,
                freshness=Freshness.FRESH,
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise provider_failure(self.id, "get_price", exc, retryable=True) from exc

    def get_klines(self, query: KlineQuery) -> KlineResult:
        try:
            from services import market_data

            rows = require_sync_result(
                market_data.get_kline_data(
                    query.symbol,
                    query.market.value,
                    query.period,
                    query.count,
                    query.start_time,
                    query.end_time,
                ),
                provider_id=self.id,
                operation="get_klines",
            )
            return KlineResult(rows=tuple(rows), source=self.id, freshness=Freshness.FRESH)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise provider_failure(self.id, "get_klines", exc, retryable=True) from exc

    def get_market_status(
        self,
        symbol: str,
        market: Market,
    ) -> MarketStatusResult:
        try:
            from services import market_data
            from services.time_source import now_utc

            raw = require_sync_result(
                market_data.get_market_status(symbol, market.value),
                provider_id=self.id,
                operation="get_market_status",
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise provider_failure(
                self.id,
                "get_market_status",
                exc,
                retryable=True,
            ) from exc
        if not isinstance(raw, Mapping) or not isinstance(raw.get("is_trading"), bool):
            raise ProviderError(
                "Market provider returned an invalid market status",
                code="PROVIDER_RESULT_INVALID",
                provider_id=self.id,
                details={"operation": "get_market_status"},
            )
        reason = raw.get("reason") or raw.get("message")
        if reason is not None and not isinstance(reason, str):
            raise ProviderError(
                "Market status reason must be text",
                code="PROVIDER_RESULT_INVALID",
                provider_id=self.id,
                details={"operation": "get_market_status"},
            )
        return MarketStatusResult(
            raw["is_trading"],
            self.id,
            now_utc(),
            reason,
        )

    def healthcheck(self) -> HealthStatus:
        def probe(timeout_seconds: float) -> bool:
            del timeout_seconds
            from services import market_data

            raw = market_data.get_market_status("BTC", Market.CRYPTO.value)
            return bool(
                isinstance(raw, Mapping)
                and isinstance(raw.get("is_trading"), bool)
                and not raw.get("error")
            )

        return run_health_probe(self.id, probe)


def create_default_market_data_port() -> MarketDataPort:
    return LegacyMarketDataAdapter()


__all__ = ["LegacyMarketDataAdapter", "create_default_market_data_port"]
