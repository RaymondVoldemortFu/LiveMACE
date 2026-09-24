"""Public JSON boundaries must reject NaN/Infinity (code-review P2).

Covers to_jsonable (contracts), LLM tool-call argument parsing and the Redis
tool cache serialization/deserialization.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchmark.contracts import to_jsonable
from benchmark.infrastructure.adapters.llm import LegacyLLMClientAdapter
from benchmark.providers import ProviderError
from services.tool_cache import RedisToolCache


NON_FINITE = [float("nan"), float("inf"), float("-inf")]


@pytest.mark.parametrize("bad", NON_FINITE, ids=["nan", "inf", "-inf"])
def test_to_jsonable_rejects_non_finite_floats(bad):
    with pytest.raises(ValueError):
        to_jsonable(bad)
    with pytest.raises(ValueError):
        to_jsonable([1.0, {"k": [bad]}])
    with pytest.raises(ValueError):
        to_jsonable({"outer": {"inner": bad}})


def test_to_jsonable_accepts_finite_numbers():
    assert to_jsonable({"a": [1.5, -2.0, 3]}) == {"a": [1.5, -2.0, 3]}


class _ToolCallClient:
    @staticmethod
    def call(**kwargs):
        raise AssertionError("not used")

    @staticmethod
    def tool_call_parts(value):
        return value.id, value.name, value.arguments


@pytest.mark.parametrize(
    "arguments_text",
    ['{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}', '{"x": 1e999}'],
)
def test_llm_tool_arguments_reject_non_finite_numbers(arguments_text):
    adapter = LegacyLLMClientAdapter(_ToolCallClient())
    call = SimpleNamespace(id="c1", name="core.tool", arguments=arguments_text)

    with pytest.raises(ProviderError) as caught:
        adapter._to_tool_call(call)
    assert caught.value.code == "LLM_TOOL_ARGUMENTS_INVALID"


def test_llm_tool_arguments_accept_standard_json():
    adapter = LegacyLLMClientAdapter(_ToolCallClient())
    call = SimpleNamespace(id="c1", name="core.tool", arguments='{"x": 1.5}')

    assert adapter._to_tool_call(call).arguments == {"x": 1.5}


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def sadd(self, *args):
        return None

    def expire(self, *args):
        return None

    def hincrby(self, *args):
        return None


def _cache_with_fake_redis():
    cache = RedisToolCache()
    fake = _FakeRedis()
    cache._get_client = lambda: fake
    return cache, fake


def test_stable_json_rejects_non_finite_numbers():
    with pytest.raises(ValueError):
        RedisToolCache._stable_json({"x": float("nan")})


def test_tool_cache_refuses_to_store_non_finite_numbers():
    cache, fake = _cache_with_fake_redis()

    stored = cache.set_json("tool", {"a": 1}, {"x": float("inf")}, 60, round_id="r1")

    assert stored is False
    assert fake.store == {}


def test_tool_cache_rejects_legacy_non_standard_json_entries():
    cache, fake = _cache_with_fake_redis()
    key = cache._build_data_key("tool", {"a": 1}, "r1")
    fake.store[key] = '{"x": NaN, "y": [Infinity, 1e999]}'

    assert cache.get_json("tool", {"a": 1}, round_id="r1") is None


def test_tool_cache_roundtrips_standard_json():
    cache, _fake = _cache_with_fake_redis()

    assert cache.set_json("tool", {"a": 1}, {"x": [1.5, "ok"]}, 60, round_id="r1")
    assert cache.get_json("tool", {"a": 1}, round_id="r1") == {"x": [1.5, "ok"]}
