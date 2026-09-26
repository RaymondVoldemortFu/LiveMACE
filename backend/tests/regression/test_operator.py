"""Operator boundary and SDK wire serialization regressions."""

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_provider_adapter_emits_plain_json_for_nested_frozen_dtos():
    from benchmark.infrastructure.adapters.llm import LegacyLLMClientAdapter
    from benchmark.providers import LLMRequest

    captured = {}

    class WireClient:
        last_finish_reason = "length"

        def call(self, **kwargs):
            captured.update(json.loads(json.dumps(kwargs)))
            return SimpleNamespace(content="ok", tool_calls=[])

        def extract_text_content(self, message):
            return message.content

    result = LegacyLLMClientAdapter(WireClient()).complete(
        LLMRequest(
            messages=(
                {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            ),
            tools=(
                {
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                    },
                },
            ),
            metadata={
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": {"type": "object"}},
                }
            },
        )
    )
    assert result.finish_reason == "length"
    assert captured["tools"][0]["function"]["parameters"]["required"] == ["value"]
    assert captured["messages"][0]["content"][0]["text"] == "hello"
    assert captured["response_format"]["json_schema"]["schema"] == {"type": "object"}


def test_operator_round_disabled_authenticated_and_shared_service(monkeypatch):
    from api.agent_routes import router
    from benchmark.application.decisions import service

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    path = "/api/agent/round"
    payload = {"account_ids": [1], "max_concurrency": 1}
    monkeypatch.delenv("DECISION_OPERATOR_TOKEN", raising=False)
    assert client.post(path, json=payload).status_code == 404
    monkeypatch.setenv("DECISION_OPERATOR_TOKEN", "operator-secret")
    assert client.post(path, json=payload).status_code == 403
    headers = {"X-Operator-Token": "operator-secret"}
    assert (
        client.post(path, json={"account_ids": []}, headers=headers).status_code == 422
    )
    assert (
        client.post(path, json={"account_ids": [-1]}, headers=headers).status_code
        == 422
    )
    assert (
        client.post(
            path, json={**payload, "max_concurrency": 5}, headers=headers
        ).status_code
        == 422
    )
    captured = []

    def run(self, request):
        captured.append(request)
        return service.DecisionRoundResult("round", 1, {})

    monkeypatch.setattr(service.DecisionRoundService, "run", run)
    response = client.post(path, json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["decision_round_id"] == "round"
    assert captured[0] == service.RunDecisionRound((1,), 1, "operator")
    monkeypatch.setattr(
        service.DecisionRoundService,
        "run",
        lambda self, request: service.DecisionRoundResult(None, 0, {}),
    )
    assert client.post(path, json=payload, headers=headers).status_code == 409


def test_readiness_reports_live_dependency_loss(monkeypatch):
    from benchmark.bootstrap.app import _register_health
    from benchmark.bootstrap import readiness

    app = FastAPI()
    app.state.runtime_handle = SimpleNamespace(is_ready=lambda: True, health=lambda: {})
    _register_health(app)
    monkeypatch.setenv("WAVE3_PRODUCTION", "true")
    monkeypatch.setattr(
        readiness,
        "production_dependencies",
        lambda: {"mysql": "ready", "redis": "unavailable"},
    )
    client = TestClient(app)
    assert client.get("/api/ready").status_code == 503
    monkeypatch.setattr(
        readiness,
        "production_dependencies",
        lambda: {"mysql": "ready", "redis": "ready"},
    )
    assert client.get("/api/ready").status_code == 200


def test_normal_asgi_shutdown_retries_until_inflight_resources_are_drained(monkeypatch):
    import anyio
    from benchmark.bootstrap import app as app_module
    from benchmark.bootstrap.runtime import RuntimeShutdownError

    handle = SimpleNamespace()
    stops = []

    async def start(context):
        return handle

    async def stop(actual):
        assert actual is handle
        stops.append(1)
        if len(stops) == 1:
            raise RuntimeShutdownError({"scheduler": "draining"})

    monkeypatch.setattr(app_module, "bootstrap_runtime", start)
    monkeypatch.setattr(app_module, "shutdown_runtime", stop)
    app = app_module.create_app(
        settings=app_module.AppSettings(shutdown_cleanup_timeout_seconds=1)
    )

    async def lifecycle():
        async with app.router.lifespan_context(app):
            pass

    anyio.run(lifecycle)
    assert len(stops) == 2


def test_sandbox_shutdown_waits_for_decision_lease_owner(monkeypatch):
    import threading
    import pytest
    from benchmark.bootstrap.tasks import default_task_descriptors
    from services import trading_commands, container_service

    lock = threading.Lock()
    monkeypatch.setattr(trading_commands, "_ai_trade_run_lock", lock)
    stopped = []
    monkeypatch.setattr(
        container_service,
        "ContainerService",
        lambda: SimpleNamespace(shutdown=lambda: stopped.append(True)),
    )
    stop = next(
        item.stop
        for item in default_task_descriptors()
        if item.task_id == "docker_sandbox"
    )
    lock.acquire()
    with pytest.raises(RuntimeError, match="still own"):
        stop()
    assert stopped == []
    lock.release()
    stop()
    assert stopped == [True]
    assert not lock.locked()
