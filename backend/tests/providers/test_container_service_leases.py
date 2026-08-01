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
