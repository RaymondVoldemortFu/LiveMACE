"""LegacyMarketDataAdapter must distinguish provider failures from a
normally closed market (code-review P2)."""

from __future__ import annotations

import pytest

from benchmark.contracts import Market
from benchmark.infrastructure.market.legacy import create_default_market_data_port
from benchmark.providers import ProviderError


def test_default_adapter_reports_normal_market_closure(monkeypatch):
    from services import market_data

    monkeypatch.setattr(
        market_data,
        "get_market_status",
        lambda symbol, market: {"is_trading": False, "reason": "market closed"},
    )

    result = create_default_market_data_port().get_market_status("BTC", Market.CRYPTO)

    assert result.is_trading is False
    assert result.reason == "market closed"


def test_default_adapter_raises_sanitized_error_for_provider_failure(monkeypatch):
    from services import market_data

    monkeypatch.setattr(
        market_data,
        "get_market_status",
        lambda symbol, market: {
            "market_status": "ERROR",
            "is_trading": False,
            "error": "secret-endpoint connection reset",
        },
    )

    adapter = create_default_market_data_port()
    with pytest.raises(ProviderError) as caught:
        adapter.get_market_status("BTC", Market.CRYPTO)

    assert caught.value.code == "PROVIDER_OPERATION_FAILED"
    assert caught.value.retryable is True
    assert caught.value.to_dict()["retryable"] is True
    # Provider messages must not leak through the stable error.
    assert "secret-endpoint" not in str(caught.value)


def test_default_adapter_raises_for_error_field_even_without_status(monkeypatch):
    from services import market_data

    monkeypatch.setattr(
        market_data,
        "get_market_status",
        lambda symbol, market: {"is_trading": False, "error": "boom"},
    )

    with pytest.raises(ProviderError) as caught:
        create_default_market_data_port().get_market_status("BTC", Market.CRYPTO)
    assert caught.value.code == "PROVIDER_OPERATION_FAILED"
