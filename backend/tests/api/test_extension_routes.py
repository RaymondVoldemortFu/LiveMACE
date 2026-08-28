from __future__ import annotations

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

    assert extensions.status_code == 200
    assert extensions.json()[0]["status"] == "loaded"
    assert agents.status_code == 200
    assert prompts.status_code == 200
    assert toolsets.json() == []
    serialized = " ".join(
        response.text for response in (extensions, agents, prompts, toolsets)
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
