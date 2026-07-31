"""Built-in market provider adapters implementing MarketDataPort."""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, Mapping

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
from benchmark.providers.runtime import provider_failure, require_sync_result
from services.time_source import now_utc


class _FunctionMarketDataAdapter(MarketDataPort):
    id = "core.market.function"
    version = "1.0.0"
    capabilities = ("market.read",)
    config_schema: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": False}

    def __init__(
        self,
        *,
        provider_id: str,
        price_loader: Callable[[str], float | None],
        kline_loader: Callable[[str, str, int, object, object], list[dict] | None],
        status_loader: Callable[[str], Mapping[str, object]],
        supported_market: Market,
    ) -> None:
        self.id = provider_id
        self._price_loader = price_loader
        self._kline_loader = kline_loader
        self._status_loader = status_loader
        self._supported_market = supported_market

    def get_price(self, symbol: str, market: Market) -> PriceResult:
        if market is not self._supported_market:
            return PriceResult(None, None, self.id, Freshness.UNAVAILABLE, f"{self.id} does not support {market.value}")
        try:
            value = require_sync_result(
                self._price_loader(symbol),
                provider_id=self.id,
                operation="get_price",
            )
            if value is None:
                return PriceResult(None, None, self.id, Freshness.UNAVAILABLE, "provider returned no price")
            decimal_value = Decimal(str(value))
            if not decimal_value.is_finite() or decimal_value <= 0:
                return PriceResult(
                    decimal_value,
                    now_utc(),
                    self.id,
                    Freshness.UNAVAILABLE,
                    "provider returned a non-positive or non-finite price",
                )
            return PriceResult(decimal_value, now_utc(), self.id, Freshness.FRESH)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise provider_failure(self.id, "get_price", exc, retryable=True) from exc

    def get_klines(self, query: KlineQuery) -> KlineResult:
        if query.market is not self._supported_market:
            return KlineResult((), self.id, Freshness.UNAVAILABLE, f"{self.id} does not support {query.market.value}")
        try:
            rows = require_sync_result(
                self._kline_loader(
                    query.symbol,
                    query.period,
                    query.count,
                    query.start_time,
                    query.end_time,
                ),
                provider_id=self.id,
                operation="get_klines",
            )
            if rows is not None and (
                not isinstance(rows, list)
                or not all(isinstance(row, Mapping) for row in rows)
            ):
                raise ProviderError(
                    "Market provider returned invalid kline rows",
                    code="PROVIDER_RESULT_INVALID",
                    provider_id=self.id,
                    details={"operation": "get_klines"},
                )
            return KlineResult(tuple(rows or ()), self.id, Freshness.FRESH if rows else Freshness.UNAVAILABLE, None if rows else "provider returned no kline data")
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
        if market is not self._supported_market:
            raise ProviderError(
                "Market provider does not support the requested market",
                code="PROVIDER_MARKET_UNSUPPORTED",
                provider_id=self.id,
                details={"market": market.value},
            )
        try:
            raw = require_sync_result(
                self._status_loader(symbol),
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
        metadata = {
            str(key): value
            for key, value in raw.items()
            if key not in {"is_trading", "reason", "message"}
        }
        return MarketStatusResult(
            is_trading=raw["is_trading"],
            source=self.id,
            as_of=now_utc(),
            reason=reason,
            metadata=metadata,
        )

    def healthcheck(self) -> HealthStatus:
        return HealthStatus("ok", self.id)


class HyperliquidMarketDataAdapter(_FunctionMarketDataAdapter):
    def __init__(self) -> None:
        from services.hyperliquid_market_data import (
            get_kline_data_from_hyperliquid,
            get_last_price_from_hyperliquid,
            get_market_status_from_hyperliquid,
        )

        super().__init__(
            provider_id="core.market.hyperliquid",
            price_loader=get_last_price_from_hyperliquid,
            kline_loader=get_kline_data_from_hyperliquid,
            status_loader=get_market_status_from_hyperliquid,
            supported_market=Market.CRYPTO,
        )


class AlpacaMarketDataAdapter(_FunctionMarketDataAdapter):
    def __init__(self) -> None:
        from services.alpaca_market_data import (
            get_kline_data_from_alpaca,
            get_last_price_from_alpaca,
            get_market_status_from_alpaca,
        )

        super().__init__(
            provider_id="core.market.alpaca",
            price_loader=get_last_price_from_alpaca,
            kline_loader=get_kline_data_from_alpaca,
            status_loader=get_market_status_from_alpaca,
            supported_market=Market.US,
        )


__all__ = ["AlpacaMarketDataAdapter", "HyperliquidMarketDataAdapter"]
