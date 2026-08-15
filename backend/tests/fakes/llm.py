"""Small OpenAI-compatible scripted fake used by current Agent tests."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable


@dataclass
class FakeToolCall:
    id: str
    name: str
    arguments: str

    @property
    def function(self):
        return SimpleNamespace(name=self.name, arguments=self.arguments)


@dataclass
class FakeLLMResponse:
    content: str | None
    tool_calls: list[FakeToolCall] | None = None


class FakeLLM:
    """Return a fixed response script and record calls without network access."""

    def __init__(
        self,
        responses: Iterable[FakeLLMResponse | BaseException],
        model: str = "fake-model",
    ):
        self._responses = list(responses)
        self.model = model
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def call(self, messages, tools=None, **kwargs):
        recorded = {"messages": list(messages), "tools": tools}
        recorded.update(kwargs)
        self.calls.append(recorded)
        if not self._responses:
            raise RuntimeError("FakeLLM response script exhausted")
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def build_assistant_message_dict(self, response: FakeLLMResponse) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": response.content}
        if response.tool_calls:
            message["tool_calls"] = response.tool_calls
        return message

    def is_gemini_model(self) -> bool:
        return False

    def close(self) -> None:
        self.closed = True
