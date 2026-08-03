"""run_health_probe must enforce a bounded wall-clock wait (code-review P2)."""

from __future__ import annotations

import time

from benchmark.providers.runtime import run_health_probe


def test_blocking_probe_is_bounded_by_the_timeout():
    def probe(timeout_seconds: float) -> bool:
        time.sleep(0.5)
        return True

    started = time.monotonic()
    status = run_health_probe("fake.provider", probe, timeout_seconds=0.05)
    elapsed = time.monotonic() - started

    assert status.status == "unavailable"
    assert "timeout" in status.message
    assert elapsed < 0.3, f"healthcheck blocked for {elapsed:.3f}s"


def test_fast_probe_reports_ok_and_receives_the_timeout():
    seen = {}

    def probe(timeout_seconds: float) -> bool:
        seen["timeout"] = timeout_seconds
        return True

    status = run_health_probe("fake.provider", probe, timeout_seconds=1.0)

    assert status.status == "ok"
    assert seen["timeout"] == 1.0


def test_probe_without_capability_reports_degraded():
    status = run_health_probe("fake.provider", lambda t: None, timeout_seconds=1.0)
    assert status.status == "degraded"


def test_probe_failure_reports_unavailable_with_error_type():
    def probe(timeout_seconds: float) -> bool:
        raise ConnectionError("down")

    status = run_health_probe("fake.provider", probe, timeout_seconds=1.0)

    assert status.status == "unavailable"
    assert status.details["error_type"] == "ConnectionError"


def test_probe_with_invalid_result_reports_unavailable():
    status = run_health_probe("fake.provider", lambda t: "yes", timeout_seconds=1.0)
    assert status.status == "unavailable"
