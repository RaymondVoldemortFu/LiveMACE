"""M12: account extension configuration.

Public surface for reading and saving an account's explicit Agent / Toolset /
Prompt runtime configuration (spec §9). Replaces the implicit
``accounts.agent_type`` + boolean-flags combination with a versioned,
validated configuration persisted in ``account_runtime_configs``.

Layout:

- ``config``: the public ``AccountExtensionConfig`` DTO plus (de)serialization
  and the legacy-account-flags -> config mapping used by the backfill.
- ``validation``: validate a config against the frozen built-in registries.
- ``service``: ``get_runtime_config`` / ``save_runtime_config`` with optimistic
  concurrency and pre-save validation.

Importing this package must not create engines, sessions or registries; all
concrete wiring resolves lazily inside functions.
"""

from benchmark.accounts.config import (
    AccountExtensionConfig,
    ConfigValidationError,
    config_from_legacy_account,
)
from benchmark.accounts.service import (
    RuntimeConfigConflictError,
    RuntimeConfigRecord,
    SaveResult,
    get_runtime_config,
    save_runtime_config,
)
from benchmark.accounts.validation import validate_extension_config

__all__ = [
    "AccountExtensionConfig",
    "ConfigValidationError",
    "config_from_legacy_account",
    "validate_extension_config",
    "get_runtime_config",
    "save_runtime_config",
    "RuntimeConfigRecord",
    "SaveResult",
    "RuntimeConfigConflictError",
]
