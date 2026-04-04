"""Unit tests for SearchSubAgent SERP parameter mapping and result normalization."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.agent.sub_agents.search_agent import SearchSubAgent


@pytest.mark.parametrize(
    "time_range,expected",
    [
        ("day", "d"),
        ("week", "w"),
        ("month", "m"),
        ("year", "y"),
        ("DAY", "d"),
        ("none", None),
        (None, None),
        ("invalid", None),
    ],
)
def test_google_recency_tbs_mapping(time_range, expected):
    assert SearchSubAgent._google_recency_tbs(time_range) == expected


@pytest.mark.parametrize(
    "query,topic,expected",
    [
        ("btc etf", "news", "btc etf news"),
        ("rates", "finance", "rates finance"),
        ("  hello  ", "general", "hello"),
        ("x", "News", "x news"),
    ],
)
def test_merge_topic_into_query(query, topic, expected):
    agent = SearchSubAgent(api_key=None)
    assert agent._merge_topic_into_query(query, topic) == expected


def test_normalize_serp_results_failed_search_result():
    agent = SearchSubAgent(api_key=None)
    payload = SimpleNamespace(success=False, error="SERP timeout")
    out = agent._normalize_serp_results(
        query="q",
        merged_query="q news",
        topic="news",
        time_range="day",
        payload=payload,
    )
    assert out["error"] == "SERP timeout"
    assert out["results"] == []
    assert out["effective_query"] == "q news"
    assert out["time_range"] == "day"


def test_normalize_serp_results_from_list_payload():
    agent = SearchSubAgent(api_key=None)
    payload = [
        {"title": "A", "link": "https://a", "description": "snippet a"},
    ]
    out = agent._normalize_serp_results(
        query="q",
        merged_query="q",
        topic="general",
        time_range="none",
        payload=payload,
    )
    assert "error" not in out
    assert len(out["results"]) == 1
    assert out["results"][0]["title"] == "A"
    assert out["results"][0]["url"] == "https://a"
    assert out["results"][0]["content"] == "snippet a"


def test_normalize_serp_results_from_dict_with_results_key():
    agent = SearchSubAgent(api_key=None)
    payload = {
        "results": [
            {"title": "T", "url": "https://u", "snippet": "s"},
        ]
    }
    out = agent._normalize_serp_results(
        query="q",
        merged_query="q",
        topic="general",
        time_range=None,
        payload=payload,
    )
    assert len(out["results"]) == 1
    assert out["results"][0]["url"] == "https://u"


def test_normalize_serp_results_skips_non_dict_items():
    agent = SearchSubAgent(api_key=None)
    payload = {"results": ["bad", {"title": "ok", "link": "https://ok"}]}
    out = agent._normalize_serp_results(
        query="q",
        merged_query="q",
        topic="general",
        time_range="none",
        payload=payload,
    )
    assert len(out["results"]) == 1
    assert out["results"][0]["title"] == "ok"


def test_search_tool_passes_time_range_to_google(monkeypatch):
    """Bright Data client receives merged query and tbs-compatible time_range in kwargs."""
    monkeypatch.setattr("config.tool_config.ToolConfig.brightdata_api_key", "x" * 32)

    google = MagicMock(
        return_value=SimpleNamespace(
            success=True,
            data=[{"title": "x", "link": "https://x", "description": "d"}],
        )
    )
    inner_client = MagicMock()
    inner_client.search.google = google

    class FakeClientCM:
        def __init__(self, token):
            self.token = token

        def __enter__(self):
            return inner_client

        def __exit__(self, *args):
            return False

    agent = SearchSubAgent(api_key=None)
    agent._brightdata_client_cls = FakeClientCM
    agent.brightdata_api_key = "x" * 32

    out = agent._search_tool("fed rates", topic="news", time_range="week", max_results=7)

    assert "error" not in out
    google.assert_called_once()
    call_kw = google.call_args.kwargs
    assert call_kw["query"] == "fed rates news"
    assert call_kw["num_results"] == 7
    assert call_kw["time_range"] == "w"


def test_search_tool_omits_time_range_when_none(monkeypatch):
    monkeypatch.setattr("config.tool_config.ToolConfig.brightdata_api_key", "x" * 32)

    google = MagicMock(
        return_value=SimpleNamespace(success=True, data=[]),
    )
    inner_client = MagicMock()
    inner_client.search.google = google

    class FakeClientCM:
        def __init__(self, token):
            pass

        def __enter__(self):
            return inner_client

        def __exit__(self, *args):
            return False

    agent = SearchSubAgent(api_key=None)
    agent._brightdata_client_cls = FakeClientCM
    agent.brightdata_api_key = "x" * 32

    agent._search_tool("only query", topic="general", time_range="none", max_results=5)
    call_kw = google.call_args.kwargs
    assert call_kw["query"] == "only query"
    assert "time_range" not in call_kw


def test_search_tool_no_brightdata_returns_error(monkeypatch):
    monkeypatch.setattr("config.tool_config.ToolConfig.brightdata_api_key", None)
    agent = SearchSubAgent(api_key=None)
    out = agent._search_tool("anything")
    assert out == {"error": "Bright Data client not initialized."}
