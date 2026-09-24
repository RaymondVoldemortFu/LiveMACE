"""Bootstrap-mutable Tool registry and per-run immutable Tool views."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from threading import RLock

from benchmark.contracts import (
    KNOWN_CAPABILITIES,
    ComponentConfigError,
    ComponentConflictError,
    ComponentNotFoundError,
    ExtensionRef,
    ToolSpec,
    require_identifier,
)

from .capabilities import has_capabilities
from .errors import ToolRegistryFrozenError
from .protocol import RegisteredTool, ToolProvider
from .validation import openai_tool_schema, validate_tool_spec


class ToolRegistry:
    """Global Tool definitions, mutable only during application bootstrap."""

    def __init__(
        self,
        *,
        trading_write_allowlist: Iterable[str] = ("core.execute_trade",),
    ) -> None:
        self._entries: dict[str, RegisteredTool] = {}
        self._frozen = False
        self._lock = RLock()
        self._trading_write_allowlist = frozenset(trading_write_allowlist)
        for name in self._trading_write_allowlist:
            require_identifier(name, "trading Tool allowlist entry")

    @property
    def frozen(self) -> bool:
        with self._lock:
            return self._frozen

    def freeze(self) -> None:
        """Permanently switch this registry to concurrent read-only mode."""

        with self._lock:
            self._frozen = True

    def register_provider(
        self,
        extension: ExtensionRef,
        provider: ToolProvider,
    ) -> None:
        """Validate and atomically register all Tools from one extension."""

        self._register_provider(extension, provider, allow_core_namespace=False)

    def register_builtin_provider(
        self,
        extension: ExtensionRef,
        provider: ToolProvider,
    ) -> None:
        """Register a provider trusted by host-controlled built-in discovery."""

        self._register_provider(extension, provider, allow_core_namespace=True)

    def _register_provider(
        self,
        extension: ExtensionRef,
        provider: ToolProvider,
        *,
        allow_core_namespace: bool,
    ) -> None:
        """Validate and atomically register all Tools from one extension."""

        with self._lock:
            self._ensure_mutable()
        if not isinstance(extension, ExtensionRef):
            raise TypeError("extension must be ExtensionRef")
        if not callable(getattr(provider, "list_tools", None)):
            raise TypeError("provider must implement list_tools()")

        tools = tuple(provider.list_tools())
        if not tools:
            raise ComponentConfigError(
                "Tool provider must expose at least one Tool",
                code="TOOL_PROVIDER_EMPTY",
                details={"extension_id": extension.id},
            )
        entries: dict[str, RegisteredTool] = {}
        for tool in tools:
            spec = self._validate_tool(tool)
            name = spec.name
            if name.startswith("core.") and not allow_core_namespace:
                raise ComponentConfigError(
                    "The core.* Tool namespace is reserved for built-in extensions",
                    code="TOOL_CORE_NAMESPACE_FORBIDDEN",
                    details={"tool_name": name, "extension_id": extension.id},
                )
            if name in entries:
                raise ComponentConflictError(
                    f"Tool provider contains duplicate name: {name}",
                    code="TOOL_PROVIDER_NAME_CONFLICT",
                    details={"tool_name": name, "extension_id": extension.id},
                )
            entries[name] = RegisteredTool(
                extension=extension,
                spec=spec,
                tool=tool,
            )

        with self._lock:
            self._ensure_mutable()
            conflicts = sorted(set(entries).intersection(self._entries))
            if conflicts:
                name = conflicts[0]
                existing = self._entries[name]
                raise ComponentConflictError(
                    f"Tool already registered: {name}",
                    code="TOOL_NAME_CONFLICT",
                    details={
                        "tool_name": name,
                        "existing_extension_id": existing.extension.id,
                        "extension_id": extension.id,
                    },
                )
            self._entries.update(entries)

    def get(self, name: str) -> RegisteredTool:
        """Resolve one Tool by its globally unique namespaced name."""

        if not isinstance(name, str):
            raise TypeError("name must be a string")
        with self._lock:
            entry = self._entries.get(name)
        if entry is None:
            raise ComponentNotFoundError(
                f"Tool not registered: {name}",
                code="TOOL_NOT_FOUND",
                details={"tool_name": name},
            )
        return entry

    def list(
        self,
        capabilities: frozenset[str] | None = None,
    ) -> tuple[ToolSpec, ...]:
        """List deterministic Tool specs visible to optional capabilities."""

        if capabilities is not None and not isinstance(capabilities, frozenset):
            raise TypeError("capabilities must be frozenset or None")
        if capabilities is not None:
            unknown = sorted(capabilities.difference(KNOWN_CAPABILITIES))
            if unknown:
                raise ComponentConfigError(
                    "Tool list contains unknown capabilities",
                    code="TOOL_CAPABILITY_UNKNOWN",
                    details={"capabilities": unknown},
                )
        with self._lock:
            entries = tuple(self._entries.values())
        specs = (
            entry.spec
            for entry in entries
            if capabilities is None or has_capabilities(entry.spec, capabilities)
        )
        return tuple(sorted(specs, key=lambda item: item.name))

    def view(
        self,
        capabilities: frozenset[str],
        enabled_tools: Iterable[str] | None = None,
    ) -> "ToolView":
        """Create an immutable per-run capability and selection view."""

        return ToolView(self, capabilities, enabled_tools)

    def _validate_tool(self, tool: object) -> ToolSpec:
        spec = getattr(tool, "spec", None)
        invoke = getattr(tool, "invoke", None)
        if not isinstance(spec, ToolSpec) or not callable(invoke):
            raise ComponentConfigError(
                "Tool provider returned an invalid Tool",
                code="TOOL_PROVIDER_TOOL_INVALID",
            )
        validate_tool_spec(
            spec,
            trading_write_allowlist=self._trading_write_allowlist,
        )
        return spec

    def _ensure_mutable(self) -> None:
        if self._frozen:
            raise ToolRegistryFrozenError("Tool registry is frozen")


@dataclass(frozen=True, init=False)
class ToolView:
    """A capability- and selection-filtered view without global mutation."""

    registry: ToolRegistry
    capabilities: frozenset[str]
    enabled_tools: frozenset[str] | None

    def __init__(
        self,
        registry: ToolRegistry,
        capabilities: frozenset[str],
        enabled_tools: Iterable[str] | None = None,
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be ToolRegistry")
        if not isinstance(capabilities, frozenset):
            raise TypeError("capabilities must be frozenset")
        unknown_capabilities = sorted(capabilities.difference(KNOWN_CAPABILITIES))
        if unknown_capabilities:
            raise ComponentConfigError(
                "Tool view contains unknown capabilities",
                code="TOOL_CAPABILITY_UNKNOWN",
                details={"capabilities": unknown_capabilities},
            )
        if isinstance(enabled_tools, str):
            raise TypeError("enabled_tools must be an iterable of Tool names")
        enabled = None if enabled_tools is None else frozenset(enabled_tools)
        if enabled is not None:
            for name in enabled:
                if not isinstance(name, str):
                    raise TypeError("enabled Tool names must be strings")
                registry.get(name)
        object.__setattr__(self, "registry", registry)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "enabled_tools", enabled)

    def get(self, name: str) -> RegisteredTool:
        """Resolve one active, authorized Tool from this view."""

        entry = self.registry.get(name)
        if self.enabled_tools is not None and name not in self.enabled_tools:
            raise ComponentNotFoundError(
                f"Tool is not active: {name}",
                code="TOOL_NOT_ACTIVE",
                details={"tool_name": name},
            )
        missing = sorted(
            frozenset(entry.spec.required_capabilities).difference(self.capabilities)
        )
        if missing:
            raise ComponentConfigError(
                f"Tool capability is not granted: {name}",
                code="TOOL_CAPABILITY_DENIED",
                details={"tool_name": name, "missing_capabilities": missing},
            )
        return entry

    def list(self) -> tuple[ToolSpec, ...]:
        """List active Tool specs authorized by this view."""

        specs = self.registry.list(self.capabilities)
        if self.enabled_tools is not None:
            specs = tuple(spec for spec in specs if spec.name in self.enabled_tools)
        return specs

    @property
    def openai_tools(self) -> tuple[dict[str, object], ...]:
        """Return model-facing schemas generated from canonical Tool specs."""

        return tuple(openai_tool_schema(spec) for spec in self.list())


__all__ = ["ToolRegistry", "ToolView"]
