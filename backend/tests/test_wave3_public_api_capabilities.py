"""Unsupported provider modalities must be absent from routing and direct calls."""

from unittest.mock import Mock

import pytest

from services.agent.public_apis_registry import _load_api_server_module


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize("name", ["markdowntoimage", "imagetotext"])
def test_image_api_discovery_and_module_loading_follow_wave3_capability(monkeypatch, production, name):
    monkeypatch.setenv("WAVE3_PRODUCTION", str(production).lower())
    server = _load_api_server_module()
    names = server._discover_available()
    assert (name in names) is not production
    assert "acronymexpander" in names
    if production:
        assert server._load_run(name) is None
    else:
        assert callable(server._load_run(name))


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize("name", ["markdowntoimage", "imagetotext"])
def test_direct_image_call_cannot_bypass_wave3_discovery(monkeypatch, production, name):
    monkeypatch.setenv("WAVE3_PRODUCTION", str(production).lower())
    server = _load_api_server_module()
    run = Mock(return_value={"status": "ok", "data": {"image_base64": "test"}})
    loader = Mock(return_value=run)
    monkeypatch.setattr(server, "_load_run", loader)
    result = server._run_api(name, {"text": "test"})
    if production:
        assert result == {"status": "error", "error": "API is unavailable for the Wave3 model provider", "data": None}
        loader.assert_not_called()
        run.assert_not_called()
    else:
        assert result["status"] == "ok"
        run.assert_called_once_with({"text": "test"})


@pytest.mark.parametrize("production", [False, True])
def test_namespaced_tools_use_same_wave3_filter(monkeypatch, production):
    from benchmark.builtin.tools.public_api import PublicApiToolsProvider
    from services.agent import public_apis_registry

    monkeypatch.setenv("WAVE3_PRODUCTION", str(production).lower())
    entries = [{"type": "function", "function": {"name": name, "parameters": {
        "type": "object", "properties": {},
    }}} for name in ("markdowntoimage", "imagetotext", "acronymexpander")]
    monkeypatch.setattr(public_apis_registry, "_load_tools_schema", lambda: entries)
    tools = PublicApiToolsProvider().list_tools()
    names = {tool.spec.name for tool in tools}
    assert ("public.markdowntoimage" in names) is not production
    assert ("public.imagetotext" in names) is not production
    assert "public.acronymexpander" in names
