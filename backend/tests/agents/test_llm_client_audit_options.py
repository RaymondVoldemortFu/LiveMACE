from __future__ import annotations

from types import SimpleNamespace

from services.agent import llm_client as llm_client_module


class FakeCompletions:
    def __init__(self):
        self.request_kwargs = None

    def create(self, **request_kwargs):
        self.request_kwargs = request_kwargs
        message = SimpleNamespace(content="ok")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeOpenAI:
    def __init__(self, **_kwargs):
        self.chat = SimpleNamespace(completions=FakeCompletions())

    def close(self):
        pass


def test_llm_client_forwards_audit_request_options(monkeypatch):
    monkeypatch.setattr(llm_client_module, "OpenAI", FakeOpenAI)
    extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
    client = llm_client_module.LLMClient(
        model="audit-model",
        api_key="dummy-key",
        extra_body=extra_body,
        reasoning_effort="none",
    )

    client.call([{"role": "user", "content": "audit"}])

    request_kwargs = client.client.chat.completions.request_kwargs
    assert request_kwargs["reasoning_effort"] == "none"
    assert request_kwargs["extra_body"] == extra_body
    assert request_kwargs["extra_body"] is not extra_body


def test_llm_client_does_not_forward_empty_audit_options(monkeypatch):
    monkeypatch.setattr(llm_client_module, "OpenAI", FakeOpenAI)
    client = llm_client_module.LLMClient(model="audit-model", api_key="dummy-key")

    client.call([{"role": "user", "content": "audit"}])

    request_kwargs = client.client.chat.completions.request_kwargs
    assert "reasoning_effort" not in request_kwargs
    assert "extra_body" not in request_kwargs
