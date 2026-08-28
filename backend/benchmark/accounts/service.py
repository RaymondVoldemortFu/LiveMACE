"""Account runtime-config read/write service (M12, spec §9).

Exposes the two operations the module task requires:

- ``get_runtime_config(account_id)``
- ``save_runtime_config(account_id, config, expected_updated_at)``

Saves validate the config against the built-in registries first (§9) and use
optimistic concurrency on ``updated_at`` so two concurrent account-worker
updates cannot silently clobber each other. Both operations run inside a
caller-provided synchronous UnitOfWork; the service never opens its own
session or commits — the UoW owns the transaction, matching the G7 rule that
every account worker uses an independent UoW.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

from benchmark.accounts.config import AccountExtensionConfig
from benchmark.accounts.validation import (
    VALIDATION_STATUS_INVALID,
    validate_extension_config,
)
from benchmark.contracts import ValidationIssue


class RuntimeConfigConflictError(RuntimeError):
    """A concurrent update changed the row since ``expected_updated_at``."""

    def __init__(self, account_id: int, expected: Any, actual: Any) -> None:
        super().__init__(
            f"runtime config for account {account_id} was modified concurrently "
            f"(expected updated_at={expected!r}, found {actual!r})"
        )
        self.account_id = account_id
        self.expected_updated_at = expected
        self.actual_updated_at = actual


@dataclass(frozen=True)
class RuntimeConfigRecord:
    """A stored config together with its validation state and lock token."""

    config: AccountExtensionConfig
    status: str
    updated_at: datetime
    issues: Tuple[ValidationIssue, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.config, AccountExtensionConfig):
            raise TypeError("config must be an AccountExtensionConfig")
        if not isinstance(self.updated_at, datetime):
            raise TypeError("updated_at must be a datetime")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        object.__setattr__(
            self,
            "updated_at",
            self.updated_at.astimezone(timezone.utc),
        )
        object.__setattr__(self, "issues", tuple(self.issues))

    @property
    def valid(self) -> bool:
        return self.status != VALIDATION_STATUS_INVALID


@dataclass(frozen=True)
class SaveResult(RuntimeConfigRecord):
    """Outcome of ``save_runtime_config`` including the next lock token."""


def get_runtime_config(uow: Any, account_id: int) -> Optional[RuntimeConfigRecord]:
    """Return the stored config record for ``account_id``, or ``None`` if unset.

    A row recorded as ``configuration_invalid`` is still returned (callers must
    check ``valid`` before running it). ``updated_at`` is the token callers must
    supply to a subsequent update; reading never mutates state.
    """
    row = uow.account_runtime_configs.get(account_id)
    if row is None:
        return None
    return RuntimeConfigRecord(
        config=_config_from_row(row),
        status=row.validation_status,
        issues=_issues_from_row(row),
        updated_at=_public_timestamp(row.updated_at),
    )


def save_runtime_config(
    uow: Any,
    account_id: int,
    config: AccountExtensionConfig,
    *,
    expected_updated_at: Optional[datetime] = None,
    agent_registry: Any = None,
    prompt_registry: Any = None,
) -> "SaveResult":
    """Validate and persist ``config`` for ``account_id``.

    Optimistic concurrency: ``expected_updated_at`` must equal the stored
    row's ``updated_at`` (or be ``None`` when creating the first row). A
    mismatch raises ``RuntimeConfigConflictError`` without writing.

    An invalid config is persisted with ``validation_status =
    configuration_invalid`` and does not raise: the account keeps its recorded
    (possibly last-valid) resources but is marked so the runtime refuses to run
    it, rather than silently falling back to another Agent (§9).
    """
    if not isinstance(config, AccountExtensionConfig):
        raise TypeError("config must be an AccountExtensionConfig")

    existing = uow.account_runtime_configs.get_for_update(account_id)
    _assert_no_conflict(account_id, existing, expected_updated_at)

    result = validate_extension_config(
        config,
        agent_registry=agent_registry,
        prompt_registry=prompt_registry,
    )
    stored_config = result.resolved_config if result.valid else config

    row = existing if existing is not None else _new_row(account_id)
    _apply_config_to_row(row, stored_config, result, existing=existing)
    uow.account_runtime_configs.upsert(row)

    return SaveResult(
        config=stored_config,
        status=result.status,
        issues=result.issues,
        updated_at=_public_timestamp(row.updated_at),
    )


def _assert_no_conflict(account_id, existing, expected_updated_at) -> None:
    actual = (
        _public_timestamp(existing.updated_at) if existing is not None else None
    )
    if existing is None:
        # Creating: caller must not claim a specific prior version.
        if expected_updated_at is not None:
            raise RuntimeConfigConflictError(account_id, expected_updated_at, None)
        return
    # Updating always requires the exact token returned by get/save. Treating
    # None as "skip the check" would turn the default argument into an
    # unconditional last-write-wins update.
    if expected_updated_at is None or actual != expected_updated_at:
        raise RuntimeConfigConflictError(account_id, expected_updated_at, actual)


def _new_row(account_id: int):
    from database.models import AccountRuntimeConfig

    return AccountRuntimeConfig(account_id=account_id)


def _apply_config_to_row(
    row,
    config: AccountExtensionConfig,
    result,
    *,
    existing,
) -> None:
    serialized = config.to_dict()
    row.agent_id = config.agent_id
    row.agent_version = config.agent_version
    row.agent_config_json = serialized["agent_config"]
    row.toolset_ids_json = serialized["toolset_ids"]
    row.disabled_tools_json = serialized["disabled_tools"]
    row.prompt_profile_id = config.prompt_profile_id
    row.prompt_profile_version = config.prompt_profile_version
    row.component_versions_json = serialized["component_versions"]
    row.validation_status = result.status
    row.validation_errors_json = [
        {
            "path": issue.path,
            "message": issue.message,
            "validator": issue.validator,
        }
        for issue in result.issues
    ]
    row.updated_at = _next_storage_timestamp(existing)


def _next_storage_timestamp(existing) -> datetime:
    """Return a strictly increasing, naive-UTC database lock token."""
    candidate = datetime.now(timezone.utc).replace(tzinfo=None)
    if existing is None or existing.updated_at is None:
        return candidate
    previous = _storage_timestamp(existing.updated_at)
    return max(candidate, previous + timedelta(microseconds=1))


def _storage_timestamp(value: datetime) -> datetime:
    """Normalize an aware/naive UTC token to the database representation."""
    if not isinstance(value, datetime):
        raise TypeError("updated_at token must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _public_timestamp(value: datetime) -> datetime:
    """Normalize a database token to timezone-aware UTC for service callers."""
    return _storage_timestamp(value).replace(tzinfo=timezone.utc)


def _config_from_row(row) -> AccountExtensionConfig:
    return AccountExtensionConfig(
        agent_id=row.agent_id,
        agent_config=row.agent_config_json or {},
        toolset_ids=row.toolset_ids_json or (),
        disabled_tools=row.disabled_tools_json or (),
        prompt_profile_id=row.prompt_profile_id,
        component_versions=row.component_versions_json or {},
    )


def _issues_from_row(row) -> Tuple[ValidationIssue, ...]:
    return tuple(
        ValidationIssue(
            path=issue["path"],
            message=issue["message"],
            validator=issue["validator"],
        )
        for issue in (row.validation_errors_json or ())
    )


__all__ = [
    "get_runtime_config",
    "save_runtime_config",
    "RuntimeConfigRecord",
    "SaveResult",
    "RuntimeConfigConflictError",
]
