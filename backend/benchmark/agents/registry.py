"""Thread-safe benchmark bootstrap registry for Agent factories."""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from typing import Any
import copy

from jsonschema import Draft202012Validator, SchemaError, validators

from .errors import (
    AgentRegistryFrozenError,
    ComponentConfigError,
    ComponentConflictError,
    ComponentNotFoundError,
)
from .protocol import (
    AgentDescriptor,
    AgentFactory,
    RegisteredAgent,
    ValidationIssue,
    ValidationReport,
    _SEMVER_RE,
    _thaw,
)


def _validator_with_defaults(schema: Mapping[str, Any]):
    base = validators.validator_for(schema, default=Draft202012Validator)
    base.check_schema(schema)
    properties = base.VALIDATORS["properties"]

    def set_defaults(validator, properties_schema, instance, full_schema):
        if isinstance(instance, dict):
            for property_name, subschema in properties_schema.items():
                if property_name not in instance and "default" in subschema:
                    instance[property_name] = copy.deepcopy(subschema["default"])
        yield from properties(validator, properties_schema, instance, full_schema)

    return validators.extend(base, {"properties": set_defaults})(schema)


def _version_key(
    version: str,
) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    match = _SEMVER_RE.fullmatch(version)
    if match is None:  # AgentDescriptor has already rejected this.
        return (0, 0, 0, 0, ((1, version),))
    major, minor, patch, prerelease = match.groups()
    prerelease_key = tuple(
        (0, int(item)) if item.isdigit() else (1, item)
        for item in (prerelease or "").split(".")
        if item
    )
    return (int(major), int(minor), int(patch), int(prerelease is None), prerelease_key)


class AgentRegistry:
    """Mutable during bootstrap, immutable and concurrently readable afterwards."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], RegisteredAgent] = {}
        self._frozen = False
        self._lock = RLock()

    @property
    def frozen(self) -> bool:
        with self._lock:
            return self._frozen

    def freeze(self) -> None:
        with self._lock:
            self._frozen = True

    def register(self, descriptor: AgentDescriptor, factory: AgentFactory) -> None:
        if not isinstance(descriptor, AgentDescriptor):
            raise TypeError("descriptor must be AgentDescriptor")
        if not callable(getattr(factory, "create", None)):
            raise TypeError("factory must provide create(context, config)")
        try:
            _validator_with_defaults(_thaw(descriptor.config_schema))
        except SchemaError as exc:
            raise ComponentConfigError(
                "agent config schema is invalid",
                code="AGENT_CONFIG_SCHEMA_INVALID",
                details={"agent_id": descriptor.id, "message": exc.message},
            ) from exc

        key = (descriptor.id, descriptor.version)
        with self._lock:
            self._ensure_mutable()
            if key in self._entries:
                raise ComponentConflictError(
                    f"agent already registered: {descriptor.id}@{descriptor.version}",
                    code="AGENT_VERSION_CONFLICT",
                    details={"agent_id": descriptor.id, "version": descriptor.version},
                )
            self._entries[key] = RegisteredAgent(descriptor, factory)

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            self._ensure_mutable()
            keys = [key for key in self._entries if key[0] == agent_id]
            if not keys:
                raise ComponentNotFoundError(
                    f"agent not registered: {agent_id}",
                    code="AGENT_NOT_FOUND",
                    details={"agent_id": agent_id},
                )
            for key in keys:
                del self._entries[key]

    def get(self, agent_id: str, version: str | None = None) -> RegisteredAgent:
        with self._lock:
            if version is not None:
                entry = self._entries.get((agent_id, version))
            else:
                candidates = [entry for key, entry in self._entries.items() if key[0] == agent_id]
                entry = max(candidates, key=lambda item: _version_key(item.descriptor.version), default=None)
            if entry is None:
                suffix = f"@{version}" if version else ""
                raise ComponentNotFoundError(
                    f"agent not registered: {agent_id}{suffix}",
                    code="AGENT_NOT_FOUND",
                    details={"agent_id": agent_id, "version": version},
                )
            return entry

    def list(self) -> tuple[AgentDescriptor, ...]:
        with self._lock:
            descriptors = (entry.descriptor for entry in self._entries.values())
            return tuple(sorted(descriptors, key=lambda item: (item.id, _version_key(item.version))))

    def validate_config(
        self,
        agent_id: str,
        config: Mapping[str, Any],
        version: str | None = None,
    ) -> ValidationReport:
        if not isinstance(config, Mapping):
            raise TypeError("config must be a mapping")
        registered = self.get(agent_id, version)
        normalized = _thaw(config)
        schema = _thaw(registered.descriptor.config_schema)
        validator = _validator_with_defaults(schema)
        errors = tuple(
            ValidationIssue(
                path=".".join(str(part) for part in error.absolute_path),
                message=error.message,
                validator=str(error.validator or ""),
            )
            for error in sorted(
                validator.iter_errors(normalized),
                key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
            )
        )
        return ValidationReport(not errors, errors=errors, normalized_config=normalized)

    def _ensure_mutable(self) -> None:
        if self._frozen:
            raise AgentRegistryFrozenError("agent registry is frozen")


__all__ = ["AgentRegistry"]
