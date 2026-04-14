import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


from api.account_routes import test_llm_connection as route_test_llm_connection
from services.agent.llm_client import LLMClient


def test_normalize_base_url_accepts_full_chat_completion_endpoint():
    assert (
        LLMClient.normalize_base_url("https://example.com/v1/chat/completions/")
        == "https://example.com/v1"
    )


def test_test_connection_reuses_llm_client_call(monkeypatch):
    captured = {}

    def fake_call(self, messages, tools=None, timeout=None, response_format=None):
        captured["messages"] = messages
        captured["tools"] = tools
        return SimpleNamespace(content="Connection test successful")

    monkeypatch.setattr(LLMClient, "call", fake_call)

    client = object.__new__(LLMClient)
    response = LLMClient.test_connection(client)

    assert response == "Connection test successful"
    assert captured["tools"] is None
    assert captured["messages"][1]["content"] == "Reply exactly with: Connection test successful"


def test_account_test_endpoint_uses_llm_client(monkeypatch):
    captured = {}

    class FakeLLMClient:
        def __init__(self, model, api_key, base_url=None):
            captured["model"] = model
            captured["api_key"] = api_key
            captured["base_url"] = base_url

        def test_connection(self, timeout_seconds=None):
            captured["called"] = True
            return "Connection test successful"

        @staticmethod
        def normalize_base_url(base_url):
            return LLMClient.normalize_base_url(base_url)

    monkeypatch.setattr("api.account_routes.LLMClient", FakeLLMClient)

    result = asyncio.run(
        route_test_llm_connection(
            {
                "model": "demo-model",
                "api_key": "demo-key",
                "base_url": "https://example.com/v1/chat/completions/",
            }
        )
    )

    assert result["success"] is True
    assert result["response"] == "Connection test successful"
    assert captured["called"] is True
    assert captured["model"] == "demo-model"
    assert captured["api_key"] == "demo-key"
    assert captured["base_url"] == "https://example.com/v1/chat/completions/"


def test_account_test_endpoint_requires_explicit_model_base_url_and_key():
    result = asyncio.run(route_test_llm_connection({}))

    assert result["success"] is False
    assert result["message"] == "Model is required"


def test_llm_client_call_retries_then_succeeds(monkeypatch):
    client = object.__new__(LLMClient)
    client.model = "gpt-4o-mini"
    client.max_retries = 2
    client._last_wire_tool_calls = None
    client.is_gemini_model = lambda: False
    client.default_timeout_seconds = 30.0

    message = SimpleNamespace(content="ok")
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    mocked_create = MagicMock(side_effect=[RuntimeError("transient"), response])
    client.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=mocked_create)
        )
    )

    out = client.call(messages=[{"role": "user", "content": "hello"}])
    assert out is message
    assert mocked_create.call_count == 2


def test_llm_client_call_raises_after_max_retries(monkeypatch):
    client = object.__new__(LLMClient)
    client.model = "gpt-4o-mini"
    client.max_retries = 1
    client._last_wire_tool_calls = None
    client.is_gemini_model = lambda: False
    client.default_timeout_seconds = 30.0

    mocked_create = MagicMock(side_effect=RuntimeError("always fails"))
    client.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=mocked_create)
        )
    )

    with pytest.raises(RuntimeError, match="always fails"):
        client.call(messages=[{"role": "user", "content": "hello"}])

    # retries=1 => total attempts=2
    assert mocked_create.call_count == 2


def test_llm_client_call_uses_max_completion_tokens_for_grok_model():
    client = object.__new__(LLMClient)
    client.model = "grok-3"
    client.max_retries = 0
    client._last_wire_tool_calls = None
    client.is_gemini_model = lambda: False
    client.is_grok_model = lambda: True
    client.default_timeout_seconds = 30.0

    message = SimpleNamespace(content="ok")
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    mocked_create = MagicMock(return_value=response)
    client.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=mocked_create)
        )
    )

    out = client.call(messages=[{"role": "user", "content": "hello"}])
    assert out is message
    kwargs = mocked_create.call_args.kwargs
    assert kwargs.get("max_completion_tokens") == 4000
    assert "max_tokens" not in kwargs


def test_llm_client_call_keeps_max_tokens_for_non_grok_model():
    client = object.__new__(LLMClient)
    client.model = "gpt-4o-mini"
    client.max_retries = 0
    client._last_wire_tool_calls = None
    client.is_gemini_model = lambda: False
    client.is_grok_model = lambda: False
    client.default_timeout_seconds = 30.0

    message = SimpleNamespace(content="ok")
    response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    mocked_create = MagicMock(return_value=response)
    client.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=mocked_create)
        )
    )

    out = client.call(messages=[{"role": "user", "content": "hello"}])
    assert out is message
    kwargs = mocked_create.call_args.kwargs
    assert kwargs.get("max_tokens") == 4000
    assert "max_completion_tokens" not in kwargs
