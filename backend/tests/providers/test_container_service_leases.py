from threading import Condition, RLock
from types import SimpleNamespace

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
