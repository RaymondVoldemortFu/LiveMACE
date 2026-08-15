"""run_health_probe must enforce a bounded wall-clock wait (code-review P2)
and never accumulate probe worker threads for a hung provider."""

from __future__ import annotations

import threading
import time

from benchmark.providers.runtime import run_health_probe


def _probe_threads(provider_id: str) -> list[threading.Thread]:
    name = f"healthcheck-{provider_id}"
    return [t for t in threading.enumerate() if t.name == name and t.is_alive()]


def test_blocking_probe_is_bounded_by_the_timeout():
    release = threading.Event()

    def probe(timeout_seconds: float) -> bool:
        release.wait(5)
        return True

    try:
        started = time.monotonic()
        status = run_health_probe("fake.provider.bounded", probe, timeout_seconds=0.05)
        elapsed = time.monotonic() - started

        assert status.status == "unavailable"
        assert "timeout" in status.message
        assert elapsed < 0.3, f"healthcheck blocked for {elapsed:.3f}s"
    finally:
        release.set()


def test_fast_probe_reports_ok_and_receives_the_timeout():
    seen = {}

    def probe(timeout_seconds: float) -> bool:
        seen["timeout"] = timeout_seconds
        return True

    status = run_health_probe("fake.provider.fast", probe, timeout_seconds=1.0)

    assert status.status == "ok"
    assert seen["timeout"] == 1.0


def test_probe_without_capability_reports_degraded():
    status = run_health_probe("fake.provider.nocap", lambda t: None, timeout_seconds=1.0)
    assert status.status == "degraded"


def test_probe_failure_reports_unavailable_with_error_type():
    def probe(timeout_seconds: float) -> bool:
        raise ConnectionError("down")

    status = run_health_probe("fake.provider.failing", probe, timeout_seconds=1.0)

    assert status.status == "unavailable"
    assert status.details["error_type"] == "ConnectionError"


def test_probe_with_invalid_result_reports_unavailable():
    status = run_health_probe("fake.provider.badresult", lambda t: "yes", timeout_seconds=1.0)
    assert status.status == "unavailable"


def test_hung_probe_does_not_accumulate_worker_threads():
    """Polling a hung dependency must keep exactly one probe thread alive."""

    provider_id = "fake.provider.hung"
    release = threading.Event()

    def probe(timeout_seconds: float) -> bool:
        release.wait(30)
        return True

    try:
        first = run_health_probe(provider_id, probe, timeout_seconds=0.05)
        assert first.status == "unavailable"
        assert "timeout" in first.message

        for _ in range(12):
            status = run_health_probe(provider_id, probe, timeout_seconds=0.05)
            assert status.status == "unavailable"
            assert status.details.get("probe_in_flight") is True

        assert len(_probe_threads(provider_id)) == 1, (
            "hung provider polling must not spawn additional worker threads"
        )
    finally:
        release.set()


def test_probe_recovers_after_previous_worker_finishes():
    provider_id = "fake.provider.recovering"
    release = threading.Event()

    def slow_probe(timeout_seconds: float) -> bool:
        release.wait(30)
        return True

    try:
        assert run_health_probe(provider_id, slow_probe, timeout_seconds=0.05).status == "unavailable"
        blocked = run_health_probe(provider_id, slow_probe, timeout_seconds=0.05)
        assert blocked.details.get("probe_in_flight") is True
    finally:
        release.set()

    # Wait for the stuck worker to actually exit before probing again.
    deadline = time.monotonic() + 5
    while _probe_threads(provider_id) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _probe_threads(provider_id), "previous worker never exited"

    status = run_health_probe(provider_id, lambda t: True, timeout_seconds=1.0)
    assert status.status == "ok"
