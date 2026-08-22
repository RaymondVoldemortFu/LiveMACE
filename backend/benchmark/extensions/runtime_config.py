"""Immutable account extension configuration and loaded runtime facade."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from benchmark.agents import AgentRegistry
from benchmark.contracts import JsonValue, require_identifier, require_semver, to_jsonable
from benchmark.contracts.common import _freeze_mapping
from benchmark.prompts import PromptRegistry
from benchmark.tools import ToolRegistry

from .discovery import ExtensionSettings

if TYPE_CHECKING:
    from .catalog import ExtensionCatalog


def _as_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or value is None:
        raise TypeError(f"{field_name} must be an iterable of identifiers")
    try:
        values = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{field_name} must be an iterable of identifiers") from exc
    if not all(isinstance(item, str) for item in values):
        raise TypeError(f"{field_name} must contain strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    for item in values:
        require_identifier(item, field_name)
    return values


def _mapping(value: object, field_name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    normalized = to_jsonable(value)
    if not isinstance(normalized, dict):
        raise TypeError(f"{field_name} must be a JSON object")
    return _freeze_mapping(normalized, field_name)


@dataclass(frozen=True)
class AccountRuntimeConfigDTO:
    """Public, persistence-neutral account component selection."""

    agent_id: str
    agent_version: str | None = None
    agent_config: Mapping[str, JsonValue] = field(default_factory=dict)
    toolset_ids: tuple[str, ...] = ()
    disabled_tools: tuple[str, ...] = ()
    prompt_profile_id: str | None = None
    prompt_profile_version: str | None = None
    component_versions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_identifier(self.agent_id, "agent_id")
        if self.agent_version is not None:
            require_semver(self.agent_version, "agent_version")
        object.__setattr__(self, "agent_config", _mapping(self.agent_config, "agent_config"))
        object.__setattr__(self, "toolset_ids", _as_tuple(self.toolset_ids, "toolset_ids"))
        object.__setattr__(
            self,
            "disabled_tools",
            _as_tuple(self.disabled_tools, "disabled_tools"),
        )
        if self.prompt_profile_id is not None:
            require_identifier(self.prompt_profile_id, "prompt_profile_id")
        if self.prompt_profile_version is not None:
            require_semver(self.prompt_profile_version, "prompt_profile_version")
        if not isinstance(self.component_versions, Mapping):
            raise TypeError("component_versions must be a mapping")
        versions: dict[str, str] = {}
        for component_id, version in self.component_versions.items():
            require_identifier(component_id, "component_versions key")
            require_semver(version, f"component_versions[{component_id}]")
            versions[component_id] = version
        object.__setattr__(
            self,
            "component_versions",
            _freeze_mapping(versions, "component_versions"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AccountRuntimeConfigDTO":
        """Parse the public JSON-shaped form without accepting unknown fields."""

        if not isinstance(value, Mapping):
            raise TypeError("account runtime config must be a mapping")
        allowed = {
            "agent_id",
            "agent_version",
            "agent_config",
            "toolset_ids",
            "disabled_tools",
            "prompt_profile_id",
            "prompt_profile_version",
            "component_versions",
        }
        unknown = sorted(set(value).difference(allowed))
        if unknown:
            raise ValueError("unknown account runtime config fields: " + ", ".join(unknown))
        return cls(
            agent_id=value.get("agent_id"),
            agent_version=value.get("agent_version"),
            agent_config=value.get("agent_config", {}),
            toolset_ids=tuple(value.get("toolset_ids", ())),
            disabled_tools=tuple(value.get("disabled_tools", ())),
            prompt_profile_id=value.get("prompt_profile_id"),
            prompt_profile_version=value.get("prompt_profile_version"),
            component_versions=value.get("component_versions", {}),
        )

    def to_mapping(self) -> dict[str, JsonValue]:
        return {
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "agent_config": dict(self.agent_config),
            "toolset_ids": list(self.toolset_ids),
            "disabled_tools": list(self.disabled_tools),
            "prompt_profile_id": self.prompt_profile_id,
            "prompt_profile_version": self.prompt_profile_version,
            "component_versions": dict(self.component_versions),
        }


@dataclass(frozen=True)
class ExtensionRuntime:
    """A complete frozen registry set and its read-only Catalog."""

    catalog: "ExtensionCatalog"
    agents: AgentRegistry = field(repr=False)
    tools: ToolRegistry = field(repr=False)
    prompts: PromptRegistry = field(repr=False)


def build_extension_runtime(settings: ExtensionSettings) -> ExtensionRuntime:
    """Discover and load one immutable extension runtime transaction."""

    if not isinstance(settings, ExtensionSettings):
        raise TypeError("settings must be ExtensionSettings")
    if settings.builtin_root is None:
        builtin_root = Path(__file__).resolve().parents[1] / "builtin"
        settings = replace(settings, builtin_root=builtin_root)

    from .catalog import ExtensionCatalog
    from .loader import load_extensions

    result = load_extensions(settings)
    catalog = ExtensionCatalog(
        result,
        allowed_capabilities=settings.allowed_capabilities,
    )
    return ExtensionRuntime(
        catalog=catalog,
        agents=result.agents,
        tools=result.tools,
        prompts=result.prompts,
    )


__all__ = [
    "AccountRuntimeConfigDTO",
    "ExtensionRuntime",
    "build_extension_runtime",
]
