"""Concurrent first-use must never return a half-initialized sandbox service."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from services import container_service as module


def test_concurrent_constructors_wait_for_complete_initialization(monkeypatch):
    class IsolatedService(module.ContainerService):
        _instance = None
        _instance_lock = Lock()

    entered, release, second_started = Event(), Event(), Event()
    client = SimpleNamespace()
    calls = []

    def from_env():
        calls.append("client")
        entered.set()
        assert release.wait(5)
        return client

    def second():
        second_started.set()
        instance = IsolatedService()
        assert instance.client is client
        assert instance._active_lease_ids == {}
        assert instance._condition is not None
        return instance

    monkeypatch.setattr(module.docker, "from_env", from_env)
    monkeypatch.setattr(IsolatedService, "_build_image_if_needed", lambda self: None)
    monkeypatch.setattr(module.atexit, "register", lambda *args: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(IsolatedService)
        try:
            assert entered.wait(5)
            follower = pool.submit(second)
            assert second_started.wait(5)
            with pytest.raises(TimeoutError):
                follower.result(timeout=0.1)
        finally:
            release.set()
        assert first.result(5) is follower.result(5)
    assert calls == ["client"]
