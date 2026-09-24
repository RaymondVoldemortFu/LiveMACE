from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.extension_routes import router
from benchmark.extensions import ExtensionSettings, build_extension_runtime
from services.extension_config_service import (
    ExtensionConfigService,
    get_extension_config_service,
)


class _NoDatabase:
    def __call__(self):
        raise AssertionError("catalog listing and validation must not open a database")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    service = ExtensionConfigService(
        runtime=build_extension_runtime(ExtensionSettings()),
        uow_factory=_NoDatabase(),
    )
    app.dependency_overrides[get_extension_config_service] = lambda: service
    return TestClient(app)


def test_catalog_routes_are_typed_and_do_not_leak_paths_or_secrets():
    with _client() as client:
        extensions = client.get("/api/extensions")
        agents = client.get("/api/extensions/agents")
        prompts = client.get("/api/extensions/prompts")
        toolsets = client.get("/api/extensions/toolsets")
        tools = client.get("/api/extensions/tools")

    assert extensions.status_code == 200
    assert extensions.json()[0]["status"] == "loaded"
    assert agents.status_code == 200
    assert prompts.status_code == 200
    assert toolsets.json()
    grouped = {name for group in toolsets.json() for name in group["tool_names"]}
    assert grouped == {tool["name"] for tool in tools.json()}
    assert tools.status_code == 200
    trade_tools = [item for item in tools.json() if item["side_effect"] == "trading_write"]
    assert trade_tools
    assert "trading.write" in trade_tools[0]["requested_capabilities"]
    serialized = " ".join(
        response.text for response in (extensions, agents, prompts, toolsets, tools)
    ).lower()
    assert "api_key" not in serialized
    assert "entrypoint" not in serialized
    assert "manifest_path" not in serialized
    assert "/home/" not in serialized


def test_validate_is_pure_and_returns_normalized_versions():
    payload = {
        "config": {
            "agent_id": "core.react",
            "agent_config": {},
            "prompt_profile_id": "core.react.default",
        }
    }
    with _client() as client:
        response = client.post("/api/account/99/runtime-config/validate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["config"]["agent_version"] == "1.0.0"
    assert body["config"]["prompt_profile_version"] == "1.0.0"


def test_component_schema_and_standard_not_found_error():
    with _client() as client:
        schema = client.get("/api/extensions/components/core.react/schema")
        missing = client.get("/api/extensions/components/core.missing/schema")

    assert schema.status_code == 200
    assert schema.json()["schema"]["type"] == "object"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "COMPONENT_NOT_FOUND"
    assert missing.json()["error"]["request_id"]


def test_default_catalog_uses_deployment_extension_settings(monkeypatch):
    extension_root = Path(__file__).resolve().parents[3] / "examples/extensions/minimal-agent"
    monkeypatch.setenv("ALPHA_ARENA_EXTENSION_DIRS", str(extension_root))
    monkeypatch.setenv("ALPHA_ARENA_DISABLED_EXTENSIONS", "benchmark.core")
    monkeypatch.setenv("ALPHA_ARENA_ALLOWED_CAPABILITIES", "market.read")
    __import__("benchmark.extensions.host", fromlist=["reset_extension_runtime"]).reset_extension_runtime()
    try:
        service = get_extension_config_service()
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            extensions = client.get("/api/extensions").json()
            agents = client.get("/api/extensions/agents").json()
            valid = client.post("/api/account/1/runtime-config/validate", json={
                "config": {"agent_id": "com.example.minimal-agent"},
            })
            disabled = client.post("/api/account/1/runtime-config/validate", json={
                "config": {"agent_id": "core.react"},
            })
        assert {item["id"]: item["status"] for item in extensions} == {
            "benchmark.core": "disabled", "com.example.minimal-agent": "loaded",
        }
        assert [item["id"] for item in agents] == ["com.example.minimal-agent"]
        assert service.catalog.allowed_capabilities == frozenset({"market.read"})
        assert valid.json()["valid"] is True
        assert disabled.json()["valid"] is False
    finally:
        __import__("benchmark.extensions.host", fromlist=["reset_extension_runtime"]).reset_extension_runtime()
