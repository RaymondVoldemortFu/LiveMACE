"""Immutable extension Manifest v1 DTOs and bounded loader."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from packaging.specifiers import InvalidSpecifier, SpecifierSet

from benchmark._structured import StructuredDataError, load_structured_file
from benchmark.contracts import (
    ExtensionManifestError,
    ExtensionRef,
    KNOWN_CAPABILITIES,
    ValidationIssue,
    require_identifier,
    require_semver,
    to_jsonable,
)

MANIFEST_FILENAME = "benchmark-extension.yaml"
_SCHEMA_PATH = Path(__file__).with_name("schema") / "manifest-v1.json"
_ENTRYPOINT_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:"
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)


def _non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _entrypoint(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _ENTRYPOINT_RE.fullmatch(value):
        raise ValueError(f"{field_name} must use module:attribute syntax")


@dataclass(frozen=True)
class PythonRequirement:
    requires: str

    def __post_init__(self) -> None:
        _non_empty(self.requires, "python.requires")
        try:
            SpecifierSet(self.requires)
        except InvalidSpecifier as exc:
            raise ValueError(
                "python.requires must be a valid version specifier"
            ) from exc


@dataclass(frozen=True)
class AgentComponent:
    id: str
    factory: str
    config_schema: str

    def __post_init__(self) -> None:
        require_identifier(self.id, "agent id")
        _entrypoint(self.factory, "agent factory")
        _non_empty(self.config_schema, "agent config_schema")


@dataclass(frozen=True)
class ToolComponent:
    provider: str

    def __post_init__(self) -> None:
        _entrypoint(self.provider, "tool provider")


@dataclass(frozen=True)
class PromptComponent:
    directory: str
    index: str

    def __post_init__(self) -> None:
        _non_empty(self.directory, "Prompt directory")
        _non_empty(self.index, "Prompt index")


@dataclass(frozen=True)
class ExtensionComponents:
    agents: tuple[AgentComponent, ...] = ()
    tools: tuple[ToolComponent, ...] = ()
    prompts: tuple[PromptComponent, ...] = ()

    def __post_init__(self) -> None:
        if not self.agents and not self.tools and not self.prompts:
            raise ValueError("an extension must declare at least one component")


@dataclass(frozen=True)
class CapabilityRequirements:
    requested: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.requested) != len(set(self.requested)):
            raise ValueError("requested capabilities must not contain duplicates")
        unknown = set(self.requested) - KNOWN_CAPABILITIES
        if unknown:
            raise ValueError(f"unknown capabilities: {', '.join(sorted(unknown))}")


@dataclass(frozen=True)
class ExtensionManifest:
    api_version: int
    id: str
    version: str
    name: str
    components: ExtensionComponents
    description: str = ""
    python: PythonRequirement | None = None
    capabilities: CapabilityRequirements = field(default_factory=CapabilityRequirements)

    def __post_init__(self) -> None:
        if self.api_version != 1:
            raise ValueError("api_version must equal 1")
        require_identifier(self.id, "extension id")
        require_semver(self.version, "extension version")
        _non_empty(self.name, "extension name")
        if not isinstance(self.description, str):
            raise TypeError("description must be a string")
        if not isinstance(self.components, ExtensionComponents):
            raise TypeError("components must be ExtensionComponents")
        if (self.components.agents or self.components.tools) and self.python is None:
            raise ValueError("python is required when agents or tools are declared")

    @property
    def ref(self) -> ExtensionRef:
        return ExtensionRef(self.id, self.version, self.api_version)


def _schema() -> Mapping[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _json_path(parts: Any) -> str:
    return ".".join(str(part) for part in parts)


def _build_manifest(raw: Mapping[str, Any]) -> ExtensionManifest:
    components = raw["components"]
    return ExtensionManifest(
        api_version=raw["api_version"],
        id=raw["id"],
        version=raw["version"],
        name=raw["name"],
        description=raw.get("description", ""),
        python=(
            PythonRequirement(raw["python"]["requires"]) if "python" in raw else None
        ),
        components=ExtensionComponents(
            agents=tuple(
                AgentComponent(item["id"], item["factory"], item["config_schema"])
                for item in components.get("agents", ())
            ),
            tools=tuple(
                ToolComponent(item["provider"]) for item in components.get("tools", ())
            ),
            prompts=tuple(
                PromptComponent(item["directory"], item["index"])
                for item in components.get("prompts", ())
            ),
        ),
        capabilities=CapabilityRequirements(
            tuple(raw.get("capabilities", {}).get("requested", ()))
        ),
    )


def _parse_manifest(
    path: Path,
) -> tuple[ExtensionManifest | None, tuple[ValidationIssue, ...]]:
    try:
        raw = load_structured_file(path)
    except StructuredDataError as exc:
        return None, (
            ValidationIssue("manifest", str(exc), code=f"MANIFEST_{exc.code}"),
        )
    if not isinstance(raw, Mapping):
        return None, (
            ValidationIssue(
                "manifest", "Manifest must be an object", code="MANIFEST_TYPE_INVALID"
            ),
        )

    issues = tuple(
        ValidationIssue(
            path=_json_path(error.absolute_path),
            message=error.message,
            validator=str(error.validator or ""),
            code="MANIFEST_SCHEMA_INVALID",
        )
        for error in Draft202012Validator(_schema()).iter_errors(raw)
    )
    if issues:
        return None, issues
    try:
        return _build_manifest(raw), ()
    except (TypeError, ValueError, KeyError) as exc:
        return None, (
            ValidationIssue("manifest", str(exc), code="MANIFEST_SEMANTIC_INVALID"),
        )


def load_manifest(path: Path) -> ExtensionManifest:
    manifest, issues = _parse_manifest(path)
    if manifest is None:
        raise ExtensionManifestError(
            "extension Manifest is invalid",
            details={"errors": [to_jsonable(issue) for issue in issues]},
        )
    return manifest


__all__ = [
    "MANIFEST_FILENAME",
    "PythonRequirement",
    "AgentComponent",
    "ToolComponent",
    "PromptComponent",
    "ExtensionComponents",
    "CapabilityRequirements",
    "ExtensionManifest",
    "load_manifest",
]
