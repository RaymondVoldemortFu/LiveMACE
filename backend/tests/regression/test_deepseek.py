"""DeepSeek thinking selection and retry bounds verified through the SDK wire."""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIError

from services.agent.llm_client import LLMClient, configured_extra_body
from services.agent.request_scope import RequestScope, use_request_scope


@pytest.fixture(autouse=True)
def runtime_settings(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "disabled")
    monkeypatch.setenv("LLM_REQUEST_MAX_RETRIES", "1")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "8")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "4096")


def completion():
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
                    "message": {"role": "assistant", "content": "OK"},
                }
            ],
        },
    )


def client_for(handler, model="deepseek-flash", extra_body=None):
    client = LLMClient(
        model, "test-only-key", "https://api.deepseek.com/v1", extra_body=extra_body
    )
    client.client._client.close()
    client.client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_explicit_thinking_wins_and_input_is_not_mutated():
    explicit = {"thinking": {"type": "enabled"}, "nested": {"values": [1]}}
    configured = configured_extra_body("deepseek-flash", explicit)
    assert configured == explicit
    configured["thinking"]["type"] = "disabled"
    configured["nested"]["values"].append(2)
    assert explicit == {"thinking": {"type": "enabled"}, "nested": {"values": [1]}}


@pytest.mark.parametrize(
    "model", ["gpt-5.6-luna", "gpt-4o-mini", "gemini-2.0-flash", "grok-3"]
)
def test_deepseek_environment_does_not_change_other_models(model):
    assert configured_extra_body(model) is None
    explicit = {"custom": {"flag": True}}
    assert configured_extra_body(model, explicit) == explicit


def test_unset_mode_preserves_deepseek_provider_default(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_THINKING_MODE")
    assert configured_extra_body("deepseek-flash") is None


def test_invalid_deepseek_mode_fails_before_request(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_THINKING_MODE", "invalid")
    with pytest.raises(ValueError, match="enabled or disabled"):
        configured_extra_body("deepseek-flash")
    assert configured_extra_body("gpt-4o-mini") is None
    assert configured_extra_body(
        "deepseek-flash", {"thinking": {"type": "enabled"}}
    ) == {"thinking": {"type": "enabled"}}


@pytest.mark.parametrize(
    "explicit,expected",
    [(None, "disabled"), ({"thinking": {"type": "enabled"}}, "enabled")],
)
def test_official_sdk_wire_thinking_mode(explicit, expected):
    requests = []

    def handler(request):
        requests.append(request)
        return completion()

    with client_for(handler, extra_body=explicit) as client:
        assert client.client.max_retries == 0
        assert client.call([{"role": "user", "content": "Reply OK"}]).content == "OK"
    request = requests[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
    assert body["thinking"] == {"type": expected}
    assert body["max_tokens"] == 4096
    assert request.extensions["timeout"]["read"] == 8


def test_other_model_wire_omits_deepseek_thinking():
    requests = []

    def handler(request):
        requests.append(request)
        return completion()

    with client_for(handler, model="gpt-4o-mini") as client:
        client.call([{"role": "user", "content": "Reply OK"}])
    assert "thinking" not in json.loads(requests[0].content)


@pytest.mark.parametrize(
    "status,code,attempts",
    [
        (500, "server_error", 2),
        (429, "rate_limit", 2),
        (429, "insufficient_quota", 1),
        (400, "invalid_request", 1),
        (401, "invalid_key", 1),
    ],
)
def test_provider_failures_have_bounded_actual_http_attempts(status, code, attempts):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={
                "error": {"message": "test provider error", "type": code, "code": code}
            },
        )

    with client_for(handler) as client:
        with pytest.raises(APIError):
            client.call([{"role": "user", "content": "Reply OK"}])
    assert len(requests) == attempts


def test_timeout_retries_are_bounded():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("test timeout", request=request)

    with client_for(handler) as client:
        with pytest.raises(APIError):
            client.call([{"role": "user", "content": "Reply OK"}])
    assert len(requests) == 2


def test_request_scope_stops_retries_and_counts_each_actual_request(monkeypatch):
    monkeypatch.setenv("LLM_REQUEST_MAX_RETRIES", "10")
    requests = []
    scope = RequestScope(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        is_cancelled=lambda: False,
        max_calls=1,
        max_output_tokens=256,
        events=SimpleNamespace(record=lambda *args: None),
    )

    def handler(request):
        requests.append(request)
        return httpx.Response(500, json={"error": {"message": "temporary failure"}})

    with client_for(handler) as client, use_request_scope(scope):
        with pytest.raises(RuntimeError, match="LLM_BUDGET_EXCEEDED"):
            client.call([{"role": "user", "content": "Reply OK"}])
    assert len(requests) == scope.calls == 1
    assert json.loads(requests[0].content)["max_tokens"] == 256


@pytest.mark.parametrize('requested,expected', [(None,4096),(512,512),(8192,4096)])
def test_worker_output_budget_reaches_sdk_wire(requested, expected):
    from benchmark.application.decisions.ports import BoundedLLM
    from benchmark.infrastructure.adapters.llm import LegacyLLMClientAdapter
    from benchmark.providers import LLMRequest
    requests = []
    def handler(request):
        requests.append(request)
        return completion()
    events = SimpleNamespace(record=lambda *args: None)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    with client_for(handler) as client:
        bounded = BoundedLLM(LegacyLLMClientAdapter(client),events,deadline,lambda:False)
        with use_request_scope(RequestScope(deadline,lambda:False,2,4096,events)):
            bounded.complete(LLMRequest(messages=({'role':'user','content':'OK'},),max_tokens=requested))
    assert json.loads(requests[0].content)['max_tokens'] == expected


def test_nested_request_budget_reports_budget_failure_without_http():
    from benchmark.application.decisions.ports import BoundedLLM
    from benchmark.infrastructure.adapters.llm import LegacyLLMClientAdapter
    from benchmark.providers import LLMRequest
    from benchmark.contracts import ProviderError
    requests = []
    events = SimpleNamespace(record=lambda *args: None)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    with client_for(lambda r: requests.append(r) or completion()) as client:
        bounded = BoundedLLM(LegacyLLMClientAdapter(client),events,deadline,lambda:False)
        with use_request_scope(RequestScope(deadline,lambda:False,0,4096,events)):
            with pytest.raises(ProviderError) as exc:
                bounded.complete(LLMRequest(messages=({'role':'user','content':'OK'},)))
        assert exc.value.code == 'LLM_BUDGET_EXCEEDED'
    assert requests == []
