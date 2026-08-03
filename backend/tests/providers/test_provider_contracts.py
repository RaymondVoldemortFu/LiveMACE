from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark.contracts import Market
from benchmark.infrastructure.adapters.llm import LegacyLLMClientAdapter
from benchmark.infrastructure.adapters.market import _FunctionMarketDataAdapter
from benchmark.infrastructure.adapters.memory import LegacyMemoryStoreAdapter
from benchmark.infrastructure.adapters.sandbox import ContainerServiceSandboxAdapter
from benchmark.providers import (
    Freshness,
    LLMClientPort,
    LLMRequest,
    LLMResponse,
    MarketDataPort,
    MemoryStorePort,
    ProviderError,
    PriceResult,
    SandboxPort,
)
from benchmark.testing import (
    FakeLLMClientPort,
    FakeMarketDataPort,
    FakeMemoryStorePort,
    FakeSandboxPort,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_contract_provider_error_carries_retryable_and_provider_id():
    from benchmark.contracts import ProviderError as ContractProviderError

    default_error = ContractProviderError("boom")
    assert default_error.code == "PROVIDER_ERROR"
    assert default_error.retryable is False
    assert default_error.provider_id == "unknown"

    explicit = ContractProviderError(
        "boom",
        code="LLM_TIMEOUT",
        retryable=True,
        provider_id="fake.llm",
    )
    assert explicit.retryable is True
    assert explicit.provider_id == "fake.llm"

    with pytest.raises(TypeError):
        ContractProviderError("boom", retryable="yes")
    with pytest.raises(ValueError):
        ContractProviderError("boom", provider_id="  ")


def test_providers_error_subclass_mirrors_fields_into_details():
    caught = ProviderError(
        "boom",
        code="PROVIDER_OPERATION_FAILED",
        retryable=True,
        provider_id="fake.market",
    )
    assert caught.retryable is True
    assert caught.provider_id == "fake.market"
    assert caught.details["retryable"] is True
    assert caught.details["provider_id"] == "fake.market"


class FakeLegacyLLM:
    model = "model-1"

    def __init__(self, message=None, error: Exception | None = None):
        self.message = message or SimpleNamespace(content="ok", tool_calls=[])
        self.error = error
        self.kwargs = None

    def call(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.message

    @staticmethod
    def extract_text_content(message):
        return message.content

    @staticmethod
    def tool_call_parts(value):
        return value.id, value.name, value.arguments

    def test_connection(self, timeout_seconds):
        return "ok"


def test_llm_adapter_forwards_options_and_removes_sdk_objects():
    tool_call = SimpleNamespace(id="call-1", name="core.echo", arguments='{"x":1}')
    client = FakeLegacyLLM(SimpleNamespace(content="done", tool_calls=[tool_call]))
    adapter = LegacyLLMClientAdapter(client)
    request = LLMRequest(
        messages=({"role": "user", "content": "hello"},),
        model="model-1",
        tools=({"type": "function", "function": {"name": "core.echo"}},),
        temperature=0.2,
        max_tokens=20,
        metadata={"timeout_seconds": 3.0},
    )

    result = adapter.complete(request)

    assert result == LLMResponse(
        content="done",
        tool_calls=result.tool_calls,
        raw={},
    )
    assert result.tool_calls[0].arguments == {"x": 1}
    assert client.kwargs["temperature"] == 0.2
    assert client.kwargs["max_tokens"] == 20
    assert client.kwargs["timeout"] == 3.0


def test_llm_adapter_rejects_model_mismatch_and_bad_tool_arguments():
    adapter = LegacyLLMClientAdapter(FakeLegacyLLM())
    with pytest.raises(ProviderError) as mismatch:
        adapter.complete(LLMRequest(messages=(), model="other"))
    assert mismatch.value.code == "LLM_MODEL_MISMATCH"

    bad = SimpleNamespace(
        content="",
        tool_calls=[SimpleNamespace(id="c", name="tool", arguments="not-json")],
    )
    adapter = LegacyLLMClientAdapter(FakeLegacyLLM(bad))
    with pytest.raises(ProviderError) as invalid:
        adapter.complete(LLMRequest(messages=(), model="model-1"))
    assert invalid.value.code == "LLM_TOOL_ARGUMENTS_INVALID"


def test_llm_provider_error_is_stable_and_does_not_leak_secret():
    adapter = LegacyLLMClientAdapter(
        FakeLegacyLLM(error=TimeoutError("api_key=super-secret"))
    )
    with pytest.raises(ProviderError) as caught:
        adapter.complete(LLMRequest(messages=(), model="model-1"))
    assert caught.value.code == "LLM_TIMEOUT"
    assert caught.value.retryable is True
    assert "super-secret" not in str(caught.value)
    assert "super-secret" not in repr(caught.value.details)


@pytest.mark.parametrize("operation", ["llm", "market", "memory", "sandbox"])
def test_adapters_reject_awaitables(operation):
    async def async_value(*args, **kwargs):
        return None

    if operation == "llm":
        client = FakeLegacyLLM()
        client.call = async_value

        def invoke():
            return LegacyLLMClientAdapter(client).complete(
                LLMRequest(messages=(), model="model-1")
            )
    elif operation == "market":
        adapter = _FunctionMarketDataAdapter(
            provider_id="fake.market.adapter",
            price_loader=async_value,
            kline_loader=lambda *args: [],
            status_loader=lambda symbol: {"is_trading": True},
            supported_market=Market.CRYPTO,
        )
        def invoke():
            return adapter.get_price("BTC", Market.CRYPTO)
    elif operation == "memory":
        store = SimpleNamespace(
            search=async_value,
            add=lambda **kwargs: "id",
            clear_account_memories=lambda **kwargs: 0,
        )
        def invoke():
            return LegacyMemoryStoreAdapter(store).search(
                1,
                "q",
                1,
                market=Market.CRYPTO,
            )
    else:
        service = SimpleNamespace(
            lease_container=async_value,
            release_container=lambda account_id, lease_id: None,
        )
        def invoke():
            return ContainerServiceSandboxAdapter(service).lease(1)

    with pytest.raises(ProviderError) as caught:
        invoke()
    assert caught.value.code == "ASYNC_PROVIDER_UNSUPPORTED"


def test_market_adapter_status_validation_and_error_mapping():
    adapter = _FunctionMarketDataAdapter(
        provider_id="fake.market.adapter",
        price_loader=lambda symbol: 10,
        kline_loader=lambda *args: [{"close": 10}],
        status_loader=lambda symbol: {"is_trading": False, "reason": "closed"},
        supported_market=Market.US,
    )
    status = adapter.get_market_status("AAPL", Market.US)
    assert status.is_trading is False
    assert status.reason == "closed"
    assert adapter.get_price("AAPL", Market.US).freshness is Freshness.FRESH

    broken = _FunctionMarketDataAdapter(
        provider_id="fake.market.broken",
        price_loader=lambda symbol: (_ for _ in ()).throw(RuntimeError("secret")),
        kline_loader=lambda *args: [],
        status_loader=lambda symbol: {"is_trading": True},
        supported_market=Market.US,
    )
    with pytest.raises(ProviderError) as caught:
        broken.get_price("AAPL", Market.US)
    assert caught.value.code == "PROVIDER_OPERATION_FAILED"
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_market_adapter_never_exposes_non_finite_price(value):
    adapter = _FunctionMarketDataAdapter(
        provider_id="fake.market.non-finite",
        price_loader=lambda symbol: value,
        kline_loader=lambda *args: [],
        status_loader=lambda symbol: {"is_trading": True},
        supported_market=Market.CRYPTO,
    )

    result = adapter.get_price("BTC", Market.CRYPTO)

    assert result.value is None
    assert result.freshness is Freshness.UNAVAILABLE


def test_price_result_rejects_non_finite_value():
    with pytest.raises(ValueError, match="finite"):
        PriceResult(
            Decimal("NaN"),
            None,
            "fake.market",
            Freshness.UNAVAILABLE,
            "bad price",
        )


def test_market_healthcheck_probes_status_loader():
    calls = []
    healthy = _FunctionMarketDataAdapter(
        provider_id="fake.market.healthy",
        price_loader=lambda symbol: 1,
        kline_loader=lambda *args: [],
        status_loader=lambda symbol: calls.append(symbol) or {"is_trading": False},
        supported_market=Market.US,
    )
    broken = _FunctionMarketDataAdapter(
        provider_id="fake.market.broken-health",
        price_loader=lambda symbol: 1,
        kline_loader=lambda *args: [],
        status_loader=lambda symbol: (_ for _ in ()).throw(RuntimeError("down")),
        supported_market=Market.CRYPTO,
    )

    assert healthy.healthcheck().status == "ok"
    assert calls == ["AAPL"]
    assert broken.healthcheck().status == "unavailable"


def test_market_status_provider_error_is_not_reported_as_closed_or_leaked():
    adapter = _FunctionMarketDataAdapter(
        provider_id="fake.market.status-error",
        price_loader=lambda symbol: 1,
        kline_loader=lambda *args: [],
        status_loader=lambda symbol: {
            "market_status": "ERROR",
            "is_trading": False,
            "error": "api_key=super-secret",
        },
        supported_market=Market.US,
    )

    with pytest.raises(ProviderError) as caught:
        adapter.get_market_status("AAPL", Market.US)

    assert caught.value.code == "PROVIDER_OPERATION_FAILED"
    assert "super-secret" not in str(caught.value)
    assert "super-secret" not in repr(caught.value.details)


def test_market_status_metadata_uses_an_explicit_allowlist():
    adapter = _FunctionMarketDataAdapter(
        provider_id="fake.market.status",
        price_loader=lambda symbol: 1,
        kline_loader=lambda *args: [],
        status_loader=lambda symbol: {
            "market_status": "OPEN",
            "is_trading": True,
            "symbol": symbol,
            "access_token": "must-not-pass",
        },
        supported_market=Market.US,
    )

    result = adapter.get_market_status("AAPL", Market.US)

    assert result.metadata == {"market_status": "OPEN", "symbol": "AAPL"}


def test_memory_and_sandbox_healthchecks_do_not_report_unprobed_ok():
    memory = LegacyMemoryStoreAdapter(
        SimpleNamespace(
            search=lambda **kwargs: [],
            add=lambda **kwargs: "id",
            clear_account_memories=lambda **kwargs: 0,
        )
    )
    sandbox = ContainerServiceSandboxAdapter(
        SimpleNamespace(
            client=None,
            lease_container=lambda account_id, lease_id: "container",
            release_container=lambda account_id, lease_id: None,
        )
    )

    assert memory.healthcheck().status == "degraded"
    assert sandbox.healthcheck().status == "unavailable"


@pytest.mark.parametrize(
    ("module_name", "adapter_name", "market"),
    [
        (
            "services.hyperliquid_market_data",
            "HyperliquidMarketDataAdapter",
            Market.CRYPTO,
        ),
        ("services.alpaca_market_data", "AlpacaMarketDataAdapter", Market.US),
    ],
)
def test_production_market_adapters_follow_the_same_contract(
    monkeypatch,
    module_name,
    adapter_name,
    market,
):
    import importlib

    monkeypatch.setenv("ALPACA_KEY", "test")
    monkeypatch.setenv("ALPACA_SECRET", "test")
    module = importlib.import_module(module_name)
    prefix = "hyperliquid" if market is Market.CRYPTO else "alpaca"
    monkeypatch.setattr(
        module,
        f"get_last_price_from_{prefix}",
        lambda symbol: 100.0,
    )
    monkeypatch.setattr(
        module,
        f"get_kline_data_from_{prefix}",
        lambda *args: [{"close": 100.0}],
    )
    monkeypatch.setattr(
        module,
        f"get_market_status_from_{prefix}",
        lambda symbol: {"is_trading": True},
    )
    adapters = importlib.import_module("benchmark.infrastructure.adapters.market")
    adapter = getattr(adapters, adapter_name)()

    assert isinstance(adapter, MarketDataPort)
    assert adapter.get_price("BTC" if market is Market.CRYPTO else "AAPL", market).value
    assert adapter.get_market_status("BTC", market).is_trading is True


def test_memory_adapter_preserves_account_market_namespace_and_rejects_bad_rows():
    calls = []

    class Store:
        def search(self, **kwargs):
            calls.append(kwargs)
            return [{"id": "m1", "content": "BTC note", "metadata": {}}]

        def add(self, **kwargs):
            calls.append(kwargs)
            return {"id": "m2"}

        def clear_account_memories(self, **kwargs):
            return 2

    adapter = LegacyMemoryStoreAdapter(Store())
    assert adapter.search(7, "BTC", 2, market=Market.CRYPTO)[0].id == "m1"
    assert adapter.add(7, "note", {}, market=Market.US) == "m2"
    assert calls[0]["account_id"] == "7"
    assert calls[0]["market"] == "CRYPTO"
    assert calls[1]["market"] == "US"

    bad = Store()
    bad.search = lambda **kwargs: [{"content": "missing id"}]
    with pytest.raises(ProviderError) as caught:
        LegacyMemoryStoreAdapter(bad).search(1, "q", 1, market=Market.CRYPTO)
    assert caught.value.code == "PROVIDER_RESULT_INVALID"


def test_memory_port_requires_explicit_market_enum():
    adapter = LegacyMemoryStoreAdapter(SimpleNamespace())
    fake = FakeMemoryStorePort()

    with pytest.raises(TypeError, match="market must be Market"):
        adapter.search(1, "q", 1, market="CRYPTO")
    with pytest.raises(TypeError, match="market must be Market"):
        adapter.add(1, "note", {}, market="US")
    with pytest.raises(TypeError, match="market must be Market"):
        fake.search(1, "q", 1, market="CRYPTO")
    with pytest.raises(TypeError, match="market must be Market"):
        fake.add(1, "note", {}, market="US")


def test_sandbox_managed_lease_releases_once_even_on_error():
    released = []
    service = SimpleNamespace(
        lease_container=lambda account_id, lease_id: f"container-{account_id}",
        release_container=lambda account_id, lease_id: released.append((account_id, lease_id)),
    )
    adapter = ContainerServiceSandboxAdapter(service)
    with pytest.raises(RuntimeError, match="boom"):
        with adapter.managed_lease(9) as lease:
            assert lease.container_id == "container-9"
            raise RuntimeError("boom")
    adapter.release(lease)
    assert released == [(9, lease.metadata["lease_id"])]


def test_sandbox_reused_container_has_a_new_releasable_lease_identity():
    released = []
    service = SimpleNamespace(
        lease_container=lambda account_id, lease_id: "reused-container",
        release_container=lambda account_id, lease_id: released.append((account_id, lease_id)),
    )
    adapter = ContainerServiceSandboxAdapter(service)

    first = adapter.lease(9)
    adapter.release(first)
    second = adapter.lease(9)
    adapter.release(second)
    adapter.release(first)
    adapter.release(second)

    assert first.container_id == second.container_id
    assert first.metadata["lease_id"] != second.metadata["lease_id"]
    assert released == [
        (9, first.metadata["lease_id"]),
        (9, second.metadata["lease_id"]),
    ]


def test_reusable_fakes_implement_every_public_port():
    assert isinstance(FakeLLMClientPort(), LLMClientPort)
    assert isinstance(FakeMemoryStorePort(), MemoryStorePort)
    assert isinstance(FakeMarketDataPort(), MarketDataPort)
    assert isinstance(FakeSandboxPort(), SandboxPort)


def test_public_provider_import_does_not_load_vendor_sdks():
    code = f"""
import sys
sys.path.insert(0, {str(BACKEND_DIR)!r})
import benchmark.providers, benchmark.testing
banned = ['openai', 'pinecone', 'alpaca', 'docker']
loaded = [name for name in banned if name in sys.modules]
assert not loaded, loaded
"""
    subprocess.run([sys.executable, "-c", code], check=True)
