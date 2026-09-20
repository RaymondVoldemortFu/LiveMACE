"""Provider boundaries, failure handling and cancellation without network access."""

import builtins
import json
import time
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config.tool_config import ToolConfig
from services.agent.sub_agents import search_agent


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(ToolConfig, "SEARCH_PROVIDER", "tavily")
    monkeypatch.setattr(ToolConfig, "tavily_api_key", "test-tavily-secret")
    monkeypatch.setattr(ToolConfig, "brightdata_api_key", "unused-brightdata-secret")
    monkeypatch.setattr(search_agent, "TIKTOKEN_AVAILABLE", False)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert name != "brightdata", "Tavily must not initialize the Bright Data SDK"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    return search_agent.SearchSubAgent()


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload if payload is not None else {"results": [
            {"title": "News", "url": "https://example.com/news", "content": "Market update"},
        ]}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def json(self):
        return self.payload


@pytest.mark.parametrize("recency", ["day", "week", "month", "year", "none"])
def test_tavily_request_recency_and_existing_result_schema(agent, monkeypatch, recency):
    response = Response()
    post = Mock(return_value=response)
    monkeypatch.setattr(search_agent.requests, "post", post)
    result = agent._search_tool("Fed rates", "news", recency, 3)
    assert result["results"] == [{"title": "News", "url": "https://example.com/news",
                                  "content": "Market update", "raw_content": "Market update"}]
    assert result["query"] == "Fed rates" and result["effective_query"] == "Fed rates news"
    assert result["topic"] == "news" and result["time_range"] == recency
    assert post.call_count == 1 and response.closed
    args, kwargs = post.call_args
    assert args == ("https://api.tavily.com/search",)
    assert kwargs["headers"] == {"Authorization": "Bearer test-tavily-secret"}
    assert "api_key" not in kwargs["json"]
    assert kwargs["json"]["max_results"] == 3
    assert kwargs["json"]["topic"] == "news"
    assert kwargs["json"].get("time_range") == (None if recency == "none" else recency)
    assert kwargs["timeout"] == (5.0, agent.search_timeout_seconds)
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_tavily_http_failures_are_single_attempt_and_do_not_echo_bodies(agent, monkeypatch, caplog, status):
    response = Response(status, {"error": "test-tavily-secret"})
    post = Mock(return_value=response)
    monkeypatch.setattr(search_agent.requests, "post", post)
    agent.max_retries = 9
    result = agent._search_tool("query")
    assert result == {"error": f"Tavily search failed (HTTP {status})."}
    assert post.call_count == 1 and response.closed
    assert "test-tavily-secret" not in caplog.text + json.dumps(result)


def test_tavily_transport_exception_is_redacted(agent, monkeypatch, caplog):
    monkeypatch.setattr(search_agent.requests, "post", Mock(side_effect=RuntimeError("test-tavily-secret")))
    result = agent._search_tool("query")
    assert result == {"error": "Tavily search failed."}
    assert "test-tavily-secret" not in caplog.text


def test_tavily_extract_uses_local_fetch_and_never_unlocker(agent, monkeypatch):
    local = {"error": "Local fetch unavailable"}
    monkeypatch.setattr(agent, "_extract_with_local_fetch", Mock(return_value=local))
    unlocker = Mock(side_effect=AssertionError("Unexpected Bright Data fallback"))
    monkeypatch.setattr(agent, "_unlocker_once", unlocker)
    assert agent._extract_tool("https://example.com") == local
    unlocker.assert_not_called()


@pytest.mark.parametrize("cancelled", [False, True])
def test_no_network_after_cancel_or_deadline(agent, monkeypatch, cancelled):
    agent.is_cancelled = lambda: cancelled
    agent.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    post = Mock()
    monkeypatch.setattr(search_agent.requests, "post", post)
    assert "error" in agent._search_tool("query")
    post.assert_not_called()


def test_cancellation_interrupts_wait_without_retrying_request(agent, monkeypatch):
    entered, cancelled, release = Event(), Event(), Event()
    agent.is_cancelled = cancelled.is_set

    def post(*args, **kwargs):
        entered.set()
        cancelled.set()
        release.wait(timeout=2)
        return Response()

    request = Mock(side_effect=post)
    monkeypatch.setattr(search_agent.requests, "post", request)
    started = time.monotonic()
    try:
        result = agent._search_tool("query")
        assert entered.is_set()
        assert "cancelled" in result["error"].lower()
        assert time.monotonic() - started < 0.5
        assert request.call_count == 1
    finally:
        release.set()


def test_run_stops_on_provider_error_and_preserves_outer_deadline(agent, monkeypatch):
    tc = {"id": "search-1", "type": "function", "function": {
        "name": "search_tool", "arguments": json.dumps({"query": "query"}),
    }}
    msg = SimpleNamespace(content="", tool_calls=[tc])
    llm = SimpleNamespace(
        model="test", call=Mock(return_value=msg),
        build_assistant_message_dict=lambda m: {"role": "assistant", "content": "", "tool_calls": m.tool_calls},
    )
    agent.llm_client = llm
    deadline = datetime.now(timezone.utc) + timedelta(seconds=5)
    agent.deadline_at = deadline
    post = Mock(return_value=Response(401))
    monkeypatch.setattr(search_agent.requests, "post", post)
    assert agent.run("query") == {"error": "Tavily search failed (HTTP 401)."}
    assert llm.call.call_count == post.call_count == 1
    assert llm.deadline_at == deadline
    assert llm.is_cancelled is agent.is_cancelled


def test_tavily_missing_key_or_invalid_arguments_never_send_request(agent, monkeypatch):
    post = Mock()
    monkeypatch.setattr(search_agent.requests, "post", post)
    for kwargs in ({"max_results": 21}, {"time_range": "invalid"}, {"query": ""}):
        assert "error" in agent._search_tool(**{"query": "query", **kwargs})
    agent.tavily_api_key = None
    assert "not configured" in agent._search_tool("query")["error"]
    post.assert_not_called()


def test_outer_deadline_bounds_http_wait_and_transport_timeout(agent, monkeypatch):
    release = Event()
    captured = []

    def post(*args, **kwargs):
        captured.append(kwargs)
        release.wait(timeout=2)
        return Response()

    monkeypatch.setattr(search_agent.requests, "post", post)
    agent.deadline_at = datetime.now(timezone.utc) + timedelta(seconds=0.15)
    started = time.monotonic()
    try:
        result = agent._search_tool("query")
        assert "deadline" in result["error"].lower()
        assert time.monotonic() - started < 0.6
        assert len(captured) == 1
        assert all(0 < timeout <= 0.15 for timeout in captured[0]["timeout"])
    finally:
        release.set()


def test_local_extract_stops_before_network_when_cancelled(agent, monkeypatch):
    agent.is_cancelled = lambda: True
    get = Mock()
    monkeypatch.setattr(search_agent.requests, "get", get)
    assert "cancelled" in agent._extract_tool("https://example.com")["error"].lower()
    get.assert_not_called()


def test_search_factory_binds_scope_and_closes_client_on_failure(monkeypatch):
    from benchmark.builtin.tools.search import _default_search_runner_factory
    from database import connection
    from repositories import account_repo
    from services.agent import request_scope
    from services.security import api_key_security

    deadline = datetime.now(timezone.utc) + timedelta(seconds=5)
    cancelled = lambda: False
    db = SimpleNamespace(close=Mock())
    monkeypatch.setattr(connection, "SessionLocal", lambda: db)
    monkeypatch.setattr(account_repo, "get_account", lambda *args: SimpleNamespace(
        model="test", api_key="encrypted-test", base_url=None, name="test",
    ))
    monkeypatch.setattr(api_key_security, "resolve_runtime_api_key", lambda _: "llm-test-key")
    monkeypatch.setattr(request_scope, "current_request_scope", lambda: SimpleNamespace(is_cancelled=cancelled))
    client = SimpleNamespace(close=Mock())
    search = SimpleNamespace(llm_client=client, run=Mock(side_effect=RuntimeError("test failure")))
    factory = Mock(return_value=search)
    monkeypatch.setattr(search_agent, "SearchSubAgent", factory)

    runner = _default_search_runner_factory(SimpleNamespace(account_id=1, deadline_at=deadline))
    db.close.assert_called_once()
    assert factory.call_args.kwargs["deadline_at"] == deadline
    assert factory.call_args.kwargs["is_cancelled"] is cancelled
    with pytest.raises(RuntimeError, match="test failure"):
        runner("query", "news", "day", 3)
    client.close.assert_called_once()
