"""Provider acceptance must match the accounts being enabled; tools stay bounded."""

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
PUBLIC_APIS = ROOT / "backend/services/agent/public-apis"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def provider(monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("WAVE3_PRODUCTION", "true")
    monkeypatch.setenv("WAVE3_MODEL", "deepseek-flash")
    monkeypatch.setenv("BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("API_KEY", "test-only-provider-key")
    monkeypatch.setenv("openai_model", "old-model")
    monkeypatch.setenv("base_url", "https://old-provider.invalid/v1")
    monkeypatch.setenv("api_key", "test-only-legacy-key")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "1024")
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "disabled")
    return {
        "passed": True,
        "model": "deepseek-flash",
        "base_url": "https://api.deepseek.com/v1",
    }


def make_client(config, handler):
    client = config.get_openai_client()
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def completion_response(content="OK"):
    return httpx.Response(
        200,
        json={
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
        },
    )


@pytest.mark.parametrize("tokens,expected", [(None, 1024), (4096, 1024), (128, 128)])
def test_public_api_uses_current_provider_and_bounded_wire_request(
    provider, tokens, expected
):
    config = load_module(PUBLIC_APIS / "config.py", "public_config_test")
    requests = []

    def handler(request):
        requests.append(request)
        return completion_response()

    with make_client(config, handler) as client:
        assert client.max_retries == 0
        kwargs = {"max_tokens": tokens} if tokens is not None else {}
        client.chat.completions.create(
            model=config.OPENAI_MODEL, messages=[], timeout=900, **kwargs
        )
    request = requests[0]
    assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-only-provider-key"
    assert json.loads(request.content)["model"] == "deepseek-flash"
    assert json.loads(request.content)["max_tokens"] == expected
    assert json.loads(request.content)["thinking"] == {"type": "disabled"}
    assert request.extensions["timeout"]["read"] == 60


def test_public_api_retains_legacy_configuration_outside_isolated_mode(provider, monkeypatch):
    monkeypatch.setenv("WAVE3_PRODUCTION", "false")
    config = load_module(PUBLIC_APIS / "config.py", "public_config_test")
    assert (config.OPENAI_MODEL, config.BASE_URL, config.API_KEY) == (
        "old-model",
        "https://old-provider.invalid/v1",
        "test-only-legacy-key",
    )


def test_public_api_obeys_parent_request_scope(provider):
    from datetime import datetime, timedelta, timezone
    from services.agent.request_scope import RequestScope, use_request_scope

    config = load_module(PUBLIC_APIS / "config.py", "public_config_test")
    requests = []
    scope = RequestScope(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        is_cancelled=lambda: False,
        max_calls=1,
        max_output_tokens=128,
        events=SimpleNamespace(record=lambda *args: None),
    )

    def handler(request):
        requests.append(request)
        return completion_response()

    with make_client(config, handler) as client, use_request_scope(scope):
        client.chat.completions.create(model=config.OPENAI_MODEL, messages=[])
        with pytest.raises(RuntimeError, match="LLM_BUDGET_EXCEEDED"):
            client.chat.completions.create(model=config.OPENAI_MODEL, messages=[])
    assert len(requests) == 1
    assert json.loads(requests[0].content)["max_tokens"] == 128
    assert requests[0].extensions["timeout"]["read"] <= 10


def test_generated_tool_loads_with_host_config_and_reaches_current_provider(
    provider, monkeypatch
):
    import config as host_config

    server = load_module(PUBLIC_APIS / "api_server.py", "public_server_test")
    public_config = server._load_config()
    requests = []

    def handler(request):
        requests.append(request)
        return completion_response(
            json.dumps({"expansions": [{"expansion": "Artificial Intelligence"}]})
        )

    with make_client(public_config, handler) as client:
        monkeypatch.setattr(public_config, "get_openai_client", lambda: client)
        result = server._run_api("acronymexpander", {"acronym": "AI"})
    assert result["status"] == "ok"
    assert sys.modules["config"] is host_config
    assert json.loads(requests[0].content)["model"] == "deepseek-flash"
    assert json.loads(requests[0].content)["thinking"] == {"type": "disabled"}


def test_public_api_explicit_thinking_overrides_environment(provider):
    config = load_module(PUBLIC_APIS / "config.py", "public_config_test")
    requests = []

    def handler(request):
        requests.append(request)
        return completion_response()

    explicit = {"thinking": {"type": "enabled"}}
    with make_client(config, handler) as client:
        client.chat.completions.create(
            model=config.OPENAI_MODEL, messages=[], extra_body=explicit
        )
    assert json.loads(requests[0].content)["thinking"] == {"type": "enabled"}
    assert explicit == {"thinking": {"type": "enabled"}}
