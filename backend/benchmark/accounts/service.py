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

from datetime import datetime, timezone
from typing import Any, Optional

from benchmark.accounts.config import AccountExtensionConfig
from benchmark.accounts.validation import (
    VALIDATION_STATUS_INVALID,
    validate_extension_config,
)


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


def get_runtime_config(uow: Any, account_id: int) -> Optional[AccountExtensionConfig]:
    """Return the stored config for ``account_id``, or ``None`` if unset.

    A row recorded as ``configuration_invalid`` is still returned (callers must
    check status before running it); reading never mutates state.
    """
    row = uow.account_runtime_configs.get(account_id)
    if row is None:
        return None
    return _config_from_row(row)


def save_runtime_config(
    uow: Any,
    account_id: int,
    config: AccountExtensionConfig,
    *,
    expected_updated_at: Optional[datetime] = None,
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

    result = validate_extension_config(config)
    stored_config = result.resolved_config if result.valid else config

    row = existing if existing is not None else _new_row(account_id)
    _apply_config_to_row(row, stored_config, result)
    uow.account_runtime_configs.upsert(row)

    return SaveResult(
        status=result.status,
        config=stored_config,
        issues=result.issues,
    )


class SaveResult:
    """Outcome of ``save_runtime_config`` (status + stored config + issues)."""

    __slots__ = ("status", "config", "issues")

    def __init__(self, status: str, config: AccountExtensionConfig, issues) -> None:
        self.status = status
        self.config = config
        self.issues = tuple(issues)

    @property
    def valid(self) -> bool:
        return self.status != VALIDATION_STATUS_INVALID


def _assert_no_conflict(account_id, existing, expected_updated_at) -> None:
    actual = getattr(existing, "updated_at", None) if existing is not None else None
    if existing is None:
        # Creating: caller must not claim a specific prior version.
        if expected_updated_at is not None:
            raise RuntimeConfigConflictError(account_id, expected_updated_at, None)
        return
    if expected_updated_at is not None and actual != expected_updated_at:
        raise RuntimeConfigConflictError(account_id, expected_updated_at, actual)


def _new_row(account_id: int):
    from database.models import AccountRuntimeConfig

    return AccountRuntimeConfig(account_id=account_id)


def _apply_config_to_row(row, config: AccountExtensionConfig, result) -> None:
    row.agent_id = config.agent_id
    row.agent_version = config.agent_version
    row.agent_config_json = dict(config.agent_config)
    row.toolset_ids_json = list(config.toolset_ids)
    row.disabled_tools_json = list(config.disabled_tools)
    row.prompt_profile_id = config.prompt_profile_id
    row.prompt_profile_version = config.prompt_profile_version
    row.component_versions_json = dict(config.component_versions)
    row.validation_status = result.status
    row.validation_errors_json = [
        {
            "path": issue.path,
            "message": issue.message,
            "validator": issue.validator,
        }
        for issue in result.issues
    ]
    # Set the optimistic-lock token explicitly. The column's server-side
    # CURRENT_TIMESTAMP is second-granularity on SQLite, so two saves within
    # the same second would otherwise collide; a microsecond-precision UTC
    # value keeps every token distinct.
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)


def _config_from_row(row) -> AccountExtensionConfig:
    return AccountExtensionConfig(
        agent_id=row.agent_id,
        agent_config=row.agent_config_json or {},
        toolset_ids=row.toolset_ids_json or (),
        disabled_tools=row.disabled_tools_json or (),
        prompt_profile_id=row.prompt_profile_id,
        component_versions=row.component_versions_json or {},
    )


__all__ = [
    "get_runtime_config",
    "save_runtime_config",
    "SaveResult",
    "RuntimeConfigConflictError",
]
