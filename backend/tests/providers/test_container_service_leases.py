from threading import Condition, RLock
from types import SimpleNamespace

import pytest

from services.container_service import ContainerService


def _service_with_one_container():
    service = object.__new__(ContainerService)
    service.client = object()
    service._condition = Condition(RLock())
    service._active_lease_ids = {}
    service.active_containers = {}
    service.idle_containers = [SimpleNamespace(id="container-1")]
    service._initialize_pool_if_needed = lambda: None
    service._sync_pool_size = lambda: None
    service._is_container_healthy = lambda container: True
    service._remove_container_quietly = lambda container: None
    return service


def test_overlapping_account_leases_release_container_only_after_last_token():
    service = _service_with_one_container()

    assert service.lease_container(1, "lease-a") == "container-1"
    assert service.lease_container(1, "lease-b") == "container-1"

    service.release_container(1, "lease-a")
    assert service.active_containers[1].id == "container-1"
    assert service.idle_containers == []

    service.release_container(1, "lease-b")
    assert 1 not in service.active_containers
    assert [container.id for container in service.idle_containers] == ["container-1"]


def _service_for_recovery(external_lease: str):
    """Account 1 holds an unhealthy container under an external lease."""
    broken = SimpleNamespace(id="container-broken")
    service = object.__new__(ContainerService)
    service.client = object()
    service._condition = Condition(RLock())
    service._active_lease_ids = {1: {external_lease}}
    service.active_containers = {1: broken}
    service.idle_containers = [SimpleNamespace(id="container-1")]
    service._initialize_pool_if_needed = lambda: None
    service._sync_pool_size = lambda: None
    service._is_container_healthy = lambda container: container.id != "container-broken"
    service._remove_container_quietly = lambda container: None
    return service


def test_recovery_keeps_surviving_external_lease_and_drops_internal_lease():
    service = _service_for_recovery("sandbox-lease-x")

    recovered = service._get_or_recover_container(1)

    assert recovered.id == "container-1"
    assert service._active_lease_ids[1] == {"sandbox-lease-x"}
    # The external owner can still release normally.
    service.release_container(1, "sandbox-lease-x")
    assert 1 not in service.active_containers
    assert [c.id for c in service.idle_containers] == ["container-1"]


def test_recovery_interleaved_with_last_lease_release_keeps_invariant():
    """Deterministic interleaving: the last external lease is released while
    the recovery flow is inside lease_container (its capacity-wait window is
    the only point where the ownership lock can be dropped)."""
    service = _service_for_recovery("sandbox-lease-x")

    original_lease_container = ContainerService.lease_container

    def lease_with_interleaved_release(account_id, lease_id=None):
        # Simulate the concurrent last release happening inside the window.
        service.release_container(1, "sandbox-lease-x")
        return original_lease_container(service, account_id, lease_id)

    service.lease_container = lease_with_interleaved_release

    recovered = service._get_or_recover_container(1)

    assert recovered is not None and recovered.id == "container-1"
    # Invariant: an active container always maps to a non-empty lease set.
    assert service.active_containers[1].id == "container-1"
    assert service._active_lease_ids.get(1), "active container lost all leases"
    # The recovered container is still releasable through the legacy path
    # without raising.
    service.release_container(1)
    assert 1 not in service.active_containers
    assert [c.id for c in service.idle_containers] == ["container-1"]


class _FakeDockerAPI:
    def __init__(self, timeout=60):
        self.timeout = timeout


class _FakeDockerClient:
    def __init__(self, leaked=()):
        self.timeout = 60
        self.api = _FakeDockerAPI(60)
        self.containers = SimpleNamespace(list=lambda **kwargs: list(leaked))


def _service_for_shutdown(*, active=None, idle=None, leaked=()):
    service = object.__new__(ContainerService)
    service.client = _FakeDockerClient(leaked=leaked)
    service._condition = Condition(RLock())
    service._active_lease_ids = {account_id: {f"lease-{account_id}"} for account_id in (active or {})}
    service.active_containers = dict(active or {})
    service.idle_containers = list(idle or [])
    service._pool_initialized = True
    return service


def test_container_shutdown_applies_timeout_and_clears_owned_containers():
    observed = []

    class Tracked:
        def __init__(self, container_id):
            self.id = container_id

        def remove(self, force=False):
            observed.append(service.client.timeout)
            observed.append(service.client.api.timeout)

    active = Tracked("active-container-1")
    idle = Tracked("idle-container-1")
    service = _service_for_shutdown(active={7: active}, idle=[idle])
    service.shutdown()

    assert service.active_containers == {}
    assert service.idle_containers == []
    assert service._pool_initialized is False
    assert observed
    assert all(0 < timeout <= ContainerService.DEFAULT_SHUTDOWN_TIMEOUT_SECONDS for timeout in observed)


def test_container_shutdown_keeps_ownership_and_raises_when_remove_fails():
    class Broken:
        id = "broken-container"

        def remove(self, force=False):
            raise TimeoutError("docker hung")

    broken = Broken()
    service = _service_for_shutdown(active={3: broken}, idle=[])

    with pytest.raises(RuntimeError, match="container shutdown incomplete"):
        service.shutdown()

    assert service.active_containers[3] is broken
    assert service._active_lease_ids[3] == {"lease-3"}
    assert service._pool_initialized is True
    assert service.client.timeout == 60
    assert service.client.api.timeout == 60


def test_container_shutdown_drops_ownership_when_container_already_gone():
    import docker.errors

    class Missing:
        id = "already-gone"

        def remove(self, force=False):
            raise docker.errors.NotFound("No such container")

    service = _service_for_shutdown(active={4: Missing()}, idle=[])
    service.shutdown()
    assert service.active_containers == {}
    assert service._active_lease_ids == {}
    assert service._pool_initialized is False


def test_container_shutdown_skips_tracked_ids_in_leaked_scan():
    removed = []

    class Tracked:
        id = "tracked-1"

        def remove(self, force=False):
            raise TimeoutError("docker hung")

    class LeakedTwin:
        id = "tracked-1"

        def remove(self, force=False):
            removed.append(self.id)

    tracked = Tracked()
    service = _service_for_shutdown(
        active={8: tracked},
        idle=[],
        leaked=[LeakedTwin()],
    )
    with pytest.raises(RuntimeError, match="container shutdown incomplete"):
        service.shutdown()
    assert removed == []
    assert service.active_containers[8] is tracked


def test_container_shutdown_propagates_leaked_list_failure():
    class Ok:
        id = "idle-ok"

        def remove(self, force=False):
            return None

    service = _service_for_shutdown(idle=[Ok()])
    service.client.containers = SimpleNamespace(
        list=lambda **kwargs: (_ for _ in ()).throw(TimeoutError("list hung"))
    )
    with pytest.raises(RuntimeError, match="list leaked containers"):
        service.shutdown()
    assert service.idle_containers == []
    assert service._pool_initialized is True


def test_container_shutdown_clears_orphan_lease_tokens():
    class Ok:
        id = "active-ok"

        def remove(self, force=False):
            return None

    service = _service_for_shutdown(active={1: Ok()}, idle=[])
    service._active_lease_ids[7] = {"stale-lease"}
    service.shutdown()
    assert service.active_containers == {}
    assert service._active_lease_ids == {}
    assert service._pool_initialized is False


def test_container_shutdown_drops_orphan_leases_when_other_remove_fails():
    class Broken:
        id = "broken-container"

        def remove(self, force=False):
            raise TimeoutError("docker hung")

    broken = Broken()
    service = _service_for_shutdown(active={3: broken}, idle=[])
    service._active_lease_ids[7] = {"stale-lease"}
    with pytest.raises(RuntimeError, match="container shutdown incomplete"):
        service.shutdown()
    assert service.active_containers[3] is broken
    assert service._active_lease_ids == {3: {"lease-3"}}



