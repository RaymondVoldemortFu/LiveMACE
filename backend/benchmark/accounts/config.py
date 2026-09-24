"""Public account extension-configuration DTO and legacy mapping (M12, spec §9).

The DTO is the only account-configuration shape crossing the application
boundary. It never carries secrets: API key / model / base_url stay on the
``accounts`` row and are resolved separately at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence

# Legacy ``agent_type`` -> public component id. ``default`` is the historical
# alias for react. Baselines are rule-based and carry no prompt profile.
_LEGACY_AGENT_IDS: dict[str, str] = {
    "react": "core.react",
    "default": "core.react",
    "multi_agent": "core.multi-agent",
    "advanced_multi_agent": "core.advanced-multi-agent",
    "rule_aware": "core.rule-aware",
    "buy_hold": "baseline.buy-hold",
    "grid": "baseline.grid",
}

# Agents that run without an LLM prompt profile.
_BASELINE_AGENT_IDS = frozenset({"baseline.buy-hold", "baseline.grid"})
_AGENT_TYPES_BY_ID = {agent_id: agent_type for agent_type, agent_id in _LEGACY_AGENT_IDS.items() if agent_type != "default"}

# Default prompt profile per non-baseline agent, ignoring react's flag matrix.
_DEFAULT_PROMPT_PROFILE: dict[str, str] = {
    "core.react": "core.react.default",
    "core.multi-agent": "core.multi-agent.default",
    "core.advanced-multi-agent": "core.advanced-multi-agent.default",
    "core.rule-aware": "core.rule-aware.default",
}


class ConfigValidationError(ValueError):
    """The provided config is structurally malformed (before registry checks)."""


@dataclass(frozen=True)
class AccountExtensionConfig:
    """Explicit, versioned Agent/Toolset/Prompt configuration for an account.

    Mirrors the public DTO in ``000-public-interface-spec.md`` §9. Frozen so a
    resolved config cannot be mutated after validation; use ``replace`` for a
    modified copy.

    Fields:

    - ``agent_id``: selected Agent component id (e.g. ``core.react``).
    - ``agent_config``: schema-validated config copy for that Agent.
    - ``toolset_ids`` / ``disabled_tools``: selected toolsets and per-account
      opt-outs.
    - ``prompt_profile_id``: selected Prompt profile (``None`` for baselines).
    - ``component_versions``: pinned versions keyed by component id, used for
      reproducible traces (§11). Empty means "latest available".
    """

    agent_id: str
    agent_config: Mapping[str, Any] = field(default_factory=dict)
    toolset_ids: Sequence[str] = field(default_factory=tuple)
    disabled_tools: Sequence[str] = field(default_factory=tuple)
    prompt_profile_id: Optional[str] = None
    component_versions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, str) or not self.agent_id.strip():
            raise ConfigValidationError("agent_id must be a non-empty string")
        if not isinstance(self.agent_config, Mapping):
            raise ConfigValidationError("agent_config must be a mapping")
        if isinstance(self.toolset_ids, str) or not _is_str_sequence(self.toolset_ids):
            raise ConfigValidationError("toolset_ids must be a sequence of strings")
        if isinstance(self.disabled_tools, str) or not _is_str_sequence(
            self.disabled_tools
        ):
            raise ConfigValidationError("disabled_tools must be a sequence of strings")
        if self.prompt_profile_id is not None and not isinstance(
            self.prompt_profile_id, str
        ):
            raise ConfigValidationError("prompt_profile_id must be a string or None")
        if not isinstance(self.component_versions, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.component_versions.items()
        ):
            raise ConfigValidationError(
                "component_versions must be a mapping of component id -> version"
            )
        # Recursively freeze JSON containers so a caller cannot mutate a
        # validated config through a nested dict/list reference.
        object.__setattr__(
            self,
            "agent_config",
            _freeze_json_value(self.agent_config, "agent_config", set()),
        )
        object.__setattr__(self, "toolset_ids", tuple(self.toolset_ids))
        object.__setattr__(self, "disabled_tools", tuple(self.disabled_tools))
        object.__setattr__(
            self,
            "component_versions",
            MappingProxyType(dict(self.component_versions)),
        )

    @property
    def is_baseline(self) -> bool:
        return self.agent_id in _BASELINE_AGENT_IDS

    @property
    def agent_version(self) -> Optional[str]:
        return self.component_versions.get(self.agent_id)

    @property
    def prompt_profile_version(self) -> Optional[str]:
        if self.prompt_profile_id is None:
            return None
        return self.component_versions.get(self.prompt_profile_id)

    def to_dict(self) -> dict[str, Any]:
        """Public JSON-serializable representation (spec §9 DTO shape)."""
        return {
            "agent_id": self.agent_id,
            "agent_config": _thaw_json_value(self.agent_config),
            "toolset_ids": list(self.toolset_ids),
            "disabled_tools": list(self.disabled_tools),
            "prompt_profile_id": self.prompt_profile_id,
            "component_versions": dict(self.component_versions),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AccountExtensionConfig":
        """Build a DTO from an untrusted mapping (API body / stored JSON)."""
        if not isinstance(data, Mapping):
            raise ConfigValidationError("config payload must be a mapping")
        return cls(
            agent_id=data.get("agent_id", ""),
            agent_config=data.get("agent_config") or {},
            toolset_ids=data.get("toolset_ids") or (),
            disabled_tools=data.get("disabled_tools") or (),
            prompt_profile_id=data.get("prompt_profile_id"),
            component_versions=data.get("component_versions") or {},
        )


def _is_str_sequence(value: Any) -> bool:
    try:
        return all(isinstance(item, str) for item in value)
    except TypeError:
        return False


def _freeze_json_value(value: Any, path: str, active: set[int]) -> Any:
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ConfigValidationError(f"{path} must not contain recursive values")
        active.add(identity)
        try:
            frozen = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ConfigValidationError(f"{path} keys must be strings")
                frozen[key] = _freeze_json_value(item, f"{path}.{key}", active)
            return MappingProxyType(frozen)
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ConfigValidationError(f"{path} must not contain recursive values")
        active.add(identity)
        try:
            return tuple(
                _freeze_json_value(item, f"{path}[{index}]", active)
                for index, item in enumerate(value)
            )
        finally:
            active.remove(identity)
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigValidationError(f"{path} must contain finite JSON numbers")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ConfigValidationError(f"{path} contains a non-JSON value")


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _flag_true(value: Any) -> bool:
    """Interpret the bool-like string/bool columns on ``accounts``."""
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _react_prompt_profile(*, memory_enabled: bool, tool_routing_enabled: bool) -> str:
    """Pick the react prompt profile matching the legacy flag matrix.

    Mirrors ``benchmark.builtin.prompts`` profile ids: the four react profiles
    map exactly onto the (memory, tool_routing) combination the current system
    renders through ``get_trade_agent_prompt``.
    """
    if memory_enabled and tool_routing_enabled:
        return "core.react.memory-tool-routing"
    if memory_enabled:
        return "core.react.memory"
    if tool_routing_enabled:
        return "core.react.tool-routing"
    return "core.react.default"


def config_from_legacy_account(account: Any) -> AccountExtensionConfig:
    """Map a legacy ``accounts`` row to an equivalent extension config.

    Preserves current behavior exactly:

    - ``enable_rule_aware`` takes precedence over ``agent_type`` (matching
      ``ai_decision_service``: a rule-aware account runs the rule-aware Agent
      regardless of its ``agent_type``).
    - react's prompt profile is selected from the ``memory_enabled`` /
      ``tool_routing_enabled`` flags.
    - baselines carry no prompt profile.

    ``component_versions`` is left empty here: the backfill records the current
    latest versions only after validation resolves them, so this mapping stays
    a pure structural translation.
    """
    agent_type = str(getattr(account, "agent_type", "react") or "react").strip().lower()
    memory_enabled = _flag_true(getattr(account, "memory_enabled", "false"))
    tool_routing_enabled = _flag_true(getattr(account, "tool_routing_enabled", "true"))
    rule_aware = _flag_true(getattr(account, "enable_rule_aware", "false"))

    if rule_aware:
        agent_id = "core.rule-aware"
    else:
        agent_id = _LEGACY_AGENT_IDS.get(agent_type)
        if agent_id is None:
            # Unknown legacy type maps by the same normalization the legacy
            # factory used; validation later flags it if unregistered.
            agent_id = f"core.{agent_type.replace('_', '-')}"

    if agent_id in _BASELINE_AGENT_IDS:
        prompt_profile_id: Optional[str] = None
    elif agent_id == "core.react":
        prompt_profile_id = _react_prompt_profile(
            memory_enabled=memory_enabled,
            tool_routing_enabled=tool_routing_enabled,
        )
    else:
        prompt_profile_id = _DEFAULT_PROMPT_PROFILE.get(agent_id)

    agent_config: dict[str, Any] = {}
    if agent_id == "core.react":
        # These are the only react knobs the legacy account carried; other
        # schema defaults are filled by the registry at validation time.
        agent_config = {
            "tool_routing_enabled": tool_routing_enabled,
            "memory_enabled": memory_enabled,
        }

    return AccountExtensionConfig(
        agent_id=agent_id,
        agent_config=agent_config,
        toolset_ids=(),
        disabled_tools=(),
        prompt_profile_id=prompt_profile_id,
        component_versions={},
    )


def mirror_legacy_account_columns(account, config: AccountExtensionConfig) -> None:
    """Keep scheduler and compliance columns aligned with the saved runtime config."""

    agent_type = _AGENT_TYPES_BY_ID.get(config.agent_id, "react")
    account.agent_type = agent_type
    account.enable_rule_aware = "true" if config.agent_id == "core.rule-aware" else "false"
    memory = config.agent_config.get("memory_enabled")
    if isinstance(memory, bool):
        account.memory_enabled = "true" if memory else "false"
    routing = config.agent_config.get("tool_routing_enabled")
    if isinstance(routing, bool):
        account.tool_routing_enabled = "true" if routing else "false"


__all__ = [
    "AccountExtensionConfig",
    "ConfigValidationError",
    "config_from_legacy_account",
    "mirror_legacy_account_columns",
]
