"""Unit tests for first-decision scheduling alignment."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.scheduler import plan_first_and_recurring_runs


def test_plan_first_and_recurring_runs_before_first_time():
    first = datetime(2026, 4, 13, 10, 12, 0, tzinfo=timezone.utc)
    now = first - timedelta(seconds=30)

    first_run, recurring_run = plan_first_and_recurring_runs(first, interval_seconds=900, now=now)

    assert first_run == first
    assert recurring_run == first + timedelta(seconds=900)


def test_plan_first_and_recurring_runs_within_first_interval_window():
    first = datetime(2026, 4, 13, 10, 12, 0, tzinfo=timezone.utc)
    # Simulate startup/setup being late by several seconds.
    now = first + timedelta(seconds=8)

    first_run, recurring_run = plan_first_and_recurring_runs(first, interval_seconds=900, now=now)

    # Keep one-off first run for misfire catch-up.
    assert first_run == first
    assert recurring_run == first + timedelta(seconds=900)


def test_plan_first_and_recurring_runs_after_first_interval_window():
    first = datetime(2026, 4, 13, 10, 12, 0, tzinfo=timezone.utc)
    now = first + timedelta(seconds=900 + 10)

    first_run, recurring_run = plan_first_and_recurring_runs(first, interval_seconds=900, now=now)

    assert first_run is None
    assert recurring_run == first + timedelta(seconds=1800)


def test_plan_first_and_recurring_runs_exactly_on_second_round_boundary():
    first = datetime(2026, 4, 13, 10, 12, 0, tzinfo=timezone.utc)
    now = first + timedelta(seconds=900)

    first_run, recurring_run = plan_first_and_recurring_runs(first, interval_seconds=900, now=now)

    # Boundary at second round: do not keep first one-off task.
    assert first_run is None
    assert recurring_run == first + timedelta(seconds=900)


def test_plan_first_and_recurring_runs_late_in_third_round_window():
    first = datetime(2026, 4, 13, 10, 12, 0, tzinfo=timezone.utc)
    # 2 full intervals passed + part of the 3rd interval window.
    now = first + timedelta(seconds=900 * 2 + 120)

    first_run, recurring_run = plan_first_and_recurring_runs(first, interval_seconds=900, now=now)

    # Non-first rounds should align to next valid interval boundary (4th slot).
    assert first_run is None
    assert recurring_run == first + timedelta(seconds=900 * 3)


def test_plan_first_and_recurring_runs_rejects_invalid_interval():
    first = datetime(2026, 4, 13, 10, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        plan_first_and_recurring_runs(first, interval_seconds=0, now=first)

