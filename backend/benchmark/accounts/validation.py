"""Validate an account extension config against the built-in registries (M12).

Spec §9: on save an account config must be validated for component existence,
version availability, config schema conformance, and (for prompts) profile
existence. A config that references a missing or unloaded component is not
rejected outright — it is recorded as ``configuration_invalid`` so the account
stops running rather than silently falling back to a different Agent (§9/§11).

This module reports issues; it does not mutate accounts or swap components.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from benchmark.accounts.config import AccountExtensionConfig
from benchmark.contracts import ComponentNotFoundError, ValidationIssue

VALIDATION_STATUS_VALID = "valid"
VALIDATION_STATUS_INVALID = "configuration_invalid"

# Baseline (rule-based) agents are not registered as LLM components; they have
# no config schema and no prompt profile to resolve.
_BASELINE_AGENT_IDS = frozenset({"baseline.buy-hold", "baseline.grid"})


@dataclass(frozen=True)
class ConfigValidationResult:
    """Outcome of validating an account config against the registries.

    - ``status``: ``valid`` or ``configuration_invalid``.
    - ``issues``: structured diagnostics (empty when valid).
    - ``resolved_config``: the config with schema defaults applied and the
      resolved component versions pinned into ``component_versions``. Present
      only when ``status`` is ``valid``.
    """

    status: str
    issues: Tuple[ValidationIssue, ...] = field(default_factory=tuple)
    resolved_config: Optional[AccountExtensionConfig] = None

    @property
    def valid(self) -> bool:
        return self.status == VALIDATION_STATUS_VALID


def _issue(path: str, message: str, validator: str = "config") -> ValidationIssue:
    return ValidationIssue(path=path, message=message, validator=validator)


def validate_extension_config(
    config: AccountExtensionConfig,
    *,
    agent_registry: Any = None,
    prompt_registry: Any = None,
) -> ConfigValidationResult:
    """Validate ``config`` against the frozen built-in registries.

    Registries default to the built-in ones; callers (tests, custom catalogs)
    may inject their own. Never raises for a config-level problem — those are
    returned as issues with ``configuration_invalid`` status. Programming
    errors (wrong argument type) still raise.
    """
    if not isinstance(config, AccountExtensionConfig):
        raise TypeError("config must be an AccountExtensionConfig")

    if config.agent_id in _BASELINE_AGENT_IDS:
        # Baselines carry no schema/profile; only reject stray prompt/config.
        issues: List[ValidationIssue] = []
        if config.prompt_profile_id is not None:
            issues.append(
                _issue(
                    "prompt_profile_id",
                    f"baseline agent {config.agent_id} takes no prompt profile",
                )
            )
        if config.agent_config:
            issues.append(
                _issue("agent_config", f"baseline agent {config.agent_id} takes no config")
            )
        if issues:
            return ConfigValidationResult(VALIDATION_STATUS_INVALID, tuple(issues))
        return ConfigValidationResult(
            VALIDATION_STATUS_VALID, resolved_config=config
        )

    if agent_registry is None:
        agent_registry = _default_agent_registry()
    if prompt_registry is None:
        from benchmark.builtin.prompts import get_builtin_prompt_registry

        prompt_registry = get_builtin_prompt_registry()

    issues = []
    resolved_versions = dict(config.component_versions)
    resolved_agent_config: Any = config.to_dict()["agent_config"]

    # --- Agent component: existence, pinned version, config schema. ---
    requested_agent_version = config.component_versions.get(config.agent_id)
    registered_agent = None
    try:
        registered_agent = agent_registry.get(config.agent_id, requested_agent_version)
    except ComponentNotFoundError:
        issues.append(
            _issue(
                "agent_id",
                f"agent component not available: {config.agent_id}"
                + (f"@{requested_agent_version}" if requested_agent_version else ""),
                validator="component",
            )
        )

    if registered_agent is not None:
        report = agent_registry.validate_config(
            config.agent_id,
            resolved_agent_config,
            requested_agent_version,
        )
        if not report.valid:
            issues.extend(
                _issue(
                    f"agent_config.{issue.path}" if issue.path else "agent_config",
                    issue.message,
                    issue.validator or "schema",
                )
                for issue in report.errors
            )
        else:
            resolved_agent_config = report.normalized_config
        resolved_versions[config.agent_id] = registered_agent.descriptor.version

    # --- Prompt profile: existence + pinned version. ---
    if config.prompt_profile_id is not None:
        requested_profile_version = config.component_versions.get(
            config.prompt_profile_id
        )
        try:
            profile = prompt_registry.get_profile(
                config.prompt_profile_id,
                version=requested_profile_version,
            )
            resolved_versions[config.prompt_profile_id] = profile.version
        except ComponentNotFoundError:
            issues.append(
                _issue(
                    "prompt_profile_id",
                    f"prompt profile not available: {config.prompt_profile_id}"
                    + (
                        f"@{requested_profile_version}"
                        if requested_profile_version
                        else ""
                    ),
                    validator="component",
                )
            )

    if issues:
        return ConfigValidationResult(VALIDATION_STATUS_INVALID, tuple(issues))

    resolved = AccountExtensionConfig(
        agent_id=config.agent_id,
        agent_config=resolved_agent_config,
        toolset_ids=config.toolset_ids,
        disabled_tools=config.disabled_tools,
        prompt_profile_id=config.prompt_profile_id,
        component_versions=resolved_versions,
    )
    return ConfigValidationResult(VALIDATION_STATUS_VALID, resolved_config=resolved)


def _default_agent_registry() -> Any:
    """Build a frozen registry of the built-in Agents.

    Mirrors ``services.agent.factory._create_legacy_registry`` so validation
    sees the same component ids production resolves, including the three agents
    still exposed through the legacy compatibility facade.
    """
    from services.agent.factory import _LEGACY_REGISTRY

    return _LEGACY_REGISTRY


__all__ = [
    "ConfigValidationResult",
    "validate_extension_config",
    "VALIDATION_STATUS_VALID",
    "VALIDATION_STATUS_INVALID",
]
