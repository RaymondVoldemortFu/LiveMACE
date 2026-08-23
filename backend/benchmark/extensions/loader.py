"""Validated, isolated loading of extension component declarations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
import importlib
from pathlib import Path
import sys
from typing import Any, Iterator

from jsonschema import Draft202012Validator, SchemaError, validators
from packaging.version import Version
from packaging.specifiers import SpecifierSet

from benchmark.agents import AgentDescriptor, AgentFactory, AgentRegistry
from benchmark.contracts import (
    BenchmarkError,
    ExtensionLoadError,
    ExtensionRef,
    JsonValue,
    PromptProfileDescriptor,
    ToolSpec,
    ValidationIssue,
    to_jsonable,
)
from benchmark.prompts import (
    LoadedPromptDirectory,
    PromptRegistry,
    PromptSourcePriority,
    load_prompt_directory,
)
from benchmark.tools import ToolProvider, ToolRegistry

from .discovery import (
    ExtensionCandidate,
    ExtensionSettings,
    ExtensionSource,
    discover_extensions,
)
from .manifest import MANIFEST_FILENAME, ExtensionManifest, _parse_manifest
from .paths import resolve_extension_path
from .validation import validate_manifest


class ExtensionStatus(str, Enum):
    LOADED = "loaded"
    DISABLED = "disabled"
    INVALID = "invalid"
    LOAD_FAILED = "load_failed"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class ExtensionLoadRecord:
    """Safe public summary of one discovered extension."""

    id: str | None
    version: str | None
    name: str | None
    source: ExtensionSource
    status: str
    capabilities: tuple[str, ...] = ()
    errors: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source, ExtensionSource):
            raise TypeError("source must be ExtensionSource")
        if self.status not in {
            ExtensionStatus.LOADED,
            ExtensionStatus.DISABLED,
            ExtensionStatus.INVALID,
            ExtensionStatus.LOAD_FAILED,
            ExtensionStatus.INCOMPATIBLE,
        }:
            raise ValueError("unsupported extension status")
        if not isinstance(self.capabilities, tuple) or not all(
            isinstance(item, str) for item in self.capabilities
        ):
            raise TypeError("capabilities must be a tuple of strings")
        if not isinstance(self.errors, tuple) or not all(
            isinstance(item, ValidationIssue) for item in self.errors
        ):
            raise TypeError("errors must be a tuple of ValidationIssue")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "source": self.source.value,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "errors": [
                {
                    "path": issue.path,
                    "code": issue.code,
                    "message": issue.message,
                }
                for issue in self.errors
            ],
        }


@dataclass(frozen=True)
class ExtensionLoadResult:
    """A frozen set of registries produced by one loader transaction."""

    records: tuple[ExtensionLoadRecord, ...]
    agents: AgentRegistry = field(repr=False, compare=False)
    tools: ToolRegistry = field(repr=False, compare=False)
    prompts: PromptRegistry = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple) or not all(
            isinstance(item, ExtensionLoadRecord) for item in self.records
        ):
            raise TypeError("records must be a tuple of ExtensionLoadRecord")
        if not isinstance(self.agents, AgentRegistry):
            raise TypeError("agents must be AgentRegistry")
        if not isinstance(self.tools, ToolRegistry):
            raise TypeError("tools must be ToolRegistry")
        if not isinstance(self.prompts, PromptRegistry):
            raise TypeError("prompts must be PromptRegistry")

    def list_extensions(self) -> tuple[ExtensionLoadRecord, ...]:
        return self.records

    @property
    def agent_registry(self) -> AgentRegistry:
        return self.agents

    @property
    def tool_registry(self) -> ToolRegistry:
        return self.tools

    @property
    def prompt_registry(self) -> PromptRegistry:
        return self.prompts


@dataclass(frozen=True)
class _AgentContribution:
    descriptor: AgentDescriptor
    factory: AgentFactory


@dataclass(frozen=True)
class _ToolContribution:
    extension: ExtensionRef
    provider: ToolProvider
    requested_capabilities: frozenset[str]
    allowed_capabilities: frozenset[str]


@dataclass(frozen=True)
class _PromptContribution:
    extension: ExtensionRef
    provider: LoadedPromptDirectory
    profiles: tuple[PromptProfileDescriptor, ...]
    priority: int


@dataclass(frozen=True)
class _ExtensionContribution:
    source: ExtensionSource
    agents: tuple[_AgentContribution, ...] = ()
    tools: tuple[_ToolContribution, ...] = ()
    prompts: tuple[_PromptContribution, ...] = ()


class _LoaderFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _issue(code: str, message: str, path: str = "extension") -> ValidationIssue:
    return ValidationIssue(path=path, message=message, code=code)


def _record(
    candidate: ExtensionCandidate,
    *,
    status: str,
    manifest: ExtensionManifest | None = None,
    errors: Sequence[ValidationIssue] = (),
) -> ExtensionLoadRecord:
    return ExtensionLoadRecord(
        id=manifest.id if manifest else None,
        version=manifest.version if manifest else None,
        name=manifest.name if manifest else None,
        source=candidate.source,
        status=status,
        capabilities=tuple(manifest.capabilities.requested) if manifest else (),
        errors=tuple(errors),
    )


def _python_compatible(manifest: ExtensionManifest) -> bool:
    if manifest.python is None:
        return True
    current = Version(
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    return SpecifierSet(manifest.python.requires).contains(current, prereleases=True)


def _preflight(
    candidate: ExtensionCandidate,
    settings: ExtensionSettings,
) -> tuple[ExtensionManifest | None, ExtensionLoadRecord | None]:
    try:
        manifest_path = resolve_extension_path(
            candidate.root, MANIFEST_FILENAME, must_exist=False
        )
    except (OSError, ValueError) as exc:
        return None, _record(
            candidate,
            status=ExtensionStatus.INVALID,
            errors=(_issue("MANIFEST_PATH_INVALID", str(exc), "manifest"),),
        )
    manifest, parse_errors = _parse_manifest(manifest_path)
    if manifest is None:
        return None, _record(candidate, status=ExtensionStatus.INVALID, errors=parse_errors)

    if manifest.id in settings.disabled_extensions:
        return manifest, _record(candidate, status=ExtensionStatus.DISABLED, manifest=manifest)

    report = validate_manifest(manifest, candidate.root)
    if not report.valid:
        return manifest, _record(
            candidate,
            status=ExtensionStatus.INVALID,
            manifest=manifest,
            errors=report.errors,
        )

    missing = sorted(
        set(manifest.capabilities.requested).difference(settings.allowed_capabilities)
    )
    if missing:
        return manifest, _record(
            candidate,
            status=ExtensionStatus.INCOMPATIBLE,
            manifest=manifest,
            errors=(
                _issue(
                    "CAPABILITY_NOT_ALLOWED",
                    "requested capabilities are not allowed: " + ", ".join(missing),
                    "capabilities.requested",
                ),
            ),
        )

    if not _python_compatible(manifest):
        return manifest, _record(
            candidate,
            status=ExtensionStatus.INCOMPATIBLE,
            manifest=manifest,
            errors=(
                _issue(
                    "PYTHON_VERSION_UNSUPPORTED",
                    "extension Python requirement is incompatible with this runtime",
                    "python.requires",
                ),
            ),
        )
    return manifest, None


@contextmanager
def _extension_import_path(root: Path) -> Iterator[None]:
    root_text = str(root)
    sys.path.insert(0, root_text)
    try:
        yield
    finally:
        try:
            sys.path.remove(root_text)
        except ValueError:
            pass


@contextmanager
def _module_import_transaction() -> Iterator[None]:
    modules_before = dict(sys.modules)
    try:
        yield
    except BaseException:
        for module_name in set(sys.modules).difference(modules_before):
            sys.modules.pop(module_name, None)
        for module_name, module in modules_before.items():
            if sys.modules.get(module_name) is not module:
                sys.modules[module_name] = module
        raise


def _import_attribute(root: Path, entrypoint: str) -> Any:
    module_name, attribute_path = entrypoint.split(":", 1)
    existing = sys.modules.get(module_name)
    replaced_module = None
    if existing is not None:
        module_file = getattr(existing, "__file__", None)
        if not module_file:
            raise _LoaderFailure("ENTRYPOINT_MODULE_INVALID", "entrypoint module has no file")
        try:
            existing_is_local = Path(module_file).resolve().is_relative_to(root)
        except OSError as exc:
            raise _LoaderFailure("ENTRYPOINT_MODULE_INVALID", "entrypoint module path is invalid") from exc
        if existing_is_local:
            module = existing
        else:
            replaced_module = existing
            del sys.modules[module_name]
            try:
                with _extension_import_path(root):
                    module = importlib.import_module(module_name)
            except Exception as exc:
                sys.modules[module_name] = replaced_module
                raise _LoaderFailure(
                    "ENTRYPOINT_IMPORT_FAILED", "declared entrypoint module could not be imported"
                ) from exc
        module_file = getattr(module, "__file__", None)
    else:
        try:
            with _extension_import_path(root):
                module = importlib.import_module(module_name)
        except Exception as exc:
            raise _LoaderFailure(
                "ENTRYPOINT_IMPORT_FAILED", "declared entrypoint module could not be imported"
            ) from exc
        module_file = getattr(module, "__file__", None)
    try:
        if not module_file:
            raise _LoaderFailure(
                "ENTRYPOINT_MODULE_INVALID", "entrypoint module has no file"
            )
        module_path = Path(module_file).resolve()
        if not module_path.is_relative_to(root.resolve()):
            raise _LoaderFailure(
                "ENTRYPOINT_MODULE_OUTSIDE_ROOT",
                "declared entrypoint module is outside the extension root",
            )
    except OSError as exc:
        raise _LoaderFailure(
            "ENTRYPOINT_MODULE_INVALID", "entrypoint module path is invalid"
        ) from exc
    except _LoaderFailure:
        if replaced_module is not None:
            sys.modules[module_name] = replaced_module
        raise

    try:
        value = module
        for part in attribute_path.split("."):
            try:
                value = getattr(value, part)
            except AttributeError as exc:
                raise _LoaderFailure(
                    "ENTRYPOINT_ATTRIBUTE_MISSING",
                    "declared entrypoint attribute is missing",
                ) from exc
        if not callable(value):
            raise _LoaderFailure(
                "ENTRYPOINT_NOT_CALLABLE", "declared entrypoint is not callable"
            )
        return value
    finally:
        if replaced_module is not None:
            sys.modules[module_name] = replaced_module


def _load_schema(root: Path, relative_path: str) -> Mapping[str, Any]:
    from benchmark._structured import StructuredDataError, load_structured_file

    try:
        path = resolve_extension_path(root, relative_path)
        raw = load_structured_file(path)
        if not isinstance(raw, Mapping):
            raise _LoaderFailure("CONFIG_SCHEMA_INVALID", "agent config schema must be an object")
        validator_type = validators.validator_for(raw, default=Draft202012Validator)
        validator_type.check_schema(raw)
        return raw
    except _LoaderFailure:
        raise
    except StructuredDataError as exc:
        raise _LoaderFailure(f"CONFIG_SCHEMA_{exc.code}", "agent config schema could not be loaded") from exc
    except (OSError, ValueError, SchemaError) as exc:
        raise _LoaderFailure("CONFIG_SCHEMA_INVALID", "agent config schema is invalid") from exc


def _load_contribution(
    candidate: ExtensionCandidate,
    manifest: ExtensionManifest,
    allowed_capabilities: frozenset[str],
) -> _ExtensionContribution:
    agents: list[_AgentContribution] = []
    tools: list[_ToolContribution] = []
    prompts: list[_PromptContribution] = []
    priority = (
        PromptSourcePriority.BUILTIN
        if candidate.source is ExtensionSource.BUILTIN
        else PromptSourcePriority.EXTERNAL
    )

    for component in manifest.components.agents:
        schema = _load_schema(candidate.root, component.config_schema)
        factory_callable = _import_attribute(candidate.root, component.factory)
        try:
            factory = factory_callable()
        except Exception as exc:
            raise _LoaderFailure("AGENT_FACTORY_FAILED", "agent factory could not be created") from exc
        if not isinstance(factory, AgentFactory):
            raise _LoaderFailure("AGENT_FACTORY_INVALID", "agent factory does not implement create()")
        agents.append(
            _AgentContribution(
                descriptor=AgentDescriptor(
                    id=component.id,
                    version=manifest.version,
                    config_schema=schema,
                ),
                factory=factory,
            )
        )

    extension = manifest.ref
    for component in manifest.components.tools:
        provider_callable = _import_attribute(candidate.root, component.provider)
        try:
            provider = provider_callable()
        except Exception as exc:
            raise _LoaderFailure("TOOL_PROVIDER_FAILED", "tool provider could not be created") from exc
        if not isinstance(provider, ToolProvider):
            raise _LoaderFailure("TOOL_PROVIDER_INVALID", "tool provider does not implement list_tools()")
        tools.append(
            _ToolContribution(
                extension=extension,
                provider=provider,
                requested_capabilities=frozenset(
                    manifest.capabilities.requested
                ),
                allowed_capabilities=allowed_capabilities,
            )
        )

    for component in manifest.components.prompts:
        prompt_root = resolve_extension_path(candidate.root, component.directory)
        index_path = resolve_extension_path(candidate.root, component.index)
        try:
            loaded = load_prompt_directory(prompt_root, index_path)
        except BenchmarkError as exc:
            raise _LoaderFailure(exc.code, "Prompt directory could not be loaded") from exc
        prompts.append(
            _PromptContribution(
                extension=extension,
                provider=loaded,
                profiles=tuple(loaded.profiles),
                priority=int(priority),
            )
        )

    return _ExtensionContribution(
        source=candidate.source,
        agents=tuple(agents),
        tools=tuple(tools),
        prompts=tuple(prompts),
    )


def _build_registries(
    contributions: Sequence[_ExtensionContribution],
) -> tuple[AgentRegistry, ToolRegistry, PromptRegistry]:
    agents = AgentRegistry()
    tools = ToolRegistry()
    prompts = PromptRegistry()
    for contribution in contributions:
        for item in contribution.agents:
            agents.register(item.descriptor, item.factory)
        for item in contribution.tools:
            existing_names = {spec.name for spec in tools.list()}
            tools.register_provider(item.extension, item.provider)
            registered_specs = tuple(
                spec for spec in tools.list() if spec.name not in existing_names
            )
            _validate_tool_capabilities(registered_specs, item)
        for item in contribution.prompts:
            prompts.register_provider(item.extension, item.provider, item.priority)
            prompts.register_profiles(item.extension, item.profiles, item.priority)
    return agents, tools, prompts


def _validate_tool_capabilities(
    specs: Sequence[ToolSpec],
    contribution: _ToolContribution,
) -> None:
    for spec in specs:
        required = frozenset(spec.required_capabilities)
        undeclared = sorted(required.difference(contribution.requested_capabilities))
        if undeclared:
            raise _LoaderFailure(
                "TOOL_CAPABILITY_UNDECLARED",
                f"Tool {spec.name} requires undeclared capabilities: "
                + ", ".join(undeclared),
            )
        disallowed = sorted(required.difference(contribution.allowed_capabilities))
        if disallowed:
            raise _LoaderFailure(
                "TOOL_CAPABILITY_NOT_ALLOWED",
                f"Tool {spec.name} requires disallowed capabilities: "
                + ", ".join(disallowed),
            )


def _build_frozen_registries(
    contributions: Sequence[_ExtensionContribution],
) -> tuple[AgentRegistry, ToolRegistry, PromptRegistry]:
    agents, tools, prompts = _build_registries(contributions)
    agents.freeze()
    tools.freeze()
    prompts.freeze()
    return agents, tools, prompts


def _failure_record(
    candidate: ExtensionCandidate,
    manifest: ExtensionManifest,
    code: str,
) -> ExtensionLoadRecord:
    return _record(
        candidate,
        status=ExtensionStatus.LOAD_FAILED,
        manifest=manifest,
        errors=(_issue(code, "extension component loading failed"),),
    )


def _fatal_builtin(records: Sequence[ExtensionLoadRecord]) -> None:
    details = {"records": [record.to_dict() for record in records]}
    raise ExtensionLoadError(
        "builtin extension loading failed",
        code="BUILTIN_EXTENSION_LOAD_FAILED",
        details=to_jsonable(details),
    )


def load_discovered_extensions(
    candidates: Sequence[ExtensionCandidate],
    settings: ExtensionSettings,
) -> ExtensionLoadResult:
    """Load candidates into fresh frozen registries without global mutation."""

    if not isinstance(settings, ExtensionSettings):
        raise TypeError("settings must be ExtensionSettings")
    candidates = tuple(candidates)

    preflight: list[
        tuple[ExtensionCandidate, ExtensionManifest | None, ExtensionLoadRecord | None]
    ] = []
    record_by_index: dict[int, ExtensionLoadRecord] = {}
    for index, candidate in enumerate(candidates):
        manifest, record = _preflight(candidate, settings)
        preflight.append((candidate, manifest, record))
        if record is not None:
            record_by_index[index] = record

    builtin_failures = [
        record
        for record in record_by_index.values()
        if record.source is ExtensionSource.BUILTIN
        and record.status not in {ExtensionStatus.DISABLED}
    ]
    if builtin_failures:
        _fatal_builtin(builtin_failures)

    accepted: list[_ExtensionContribution] = []
    staged_registries: tuple[AgentRegistry, ToolRegistry, PromptRegistry] | None = None
    for index, (candidate, manifest, preflight_record) in enumerate(preflight):
        if preflight_record is not None:
            continue
        if manifest is None:
            continue
        try:
            with _module_import_transaction():
                contribution = _load_contribution(
                    candidate, manifest, settings.allowed_capabilities
                )
                candidate_registries = _build_frozen_registries(
                    [*accepted, contribution]
                )
        except _LoaderFailure as exc:
            failure = _failure_record(candidate, manifest, exc.code)
            record_by_index[index] = failure
            if candidate.source is ExtensionSource.BUILTIN:
                _fatal_builtin(tuple(record_by_index.values()))
            continue
        except BenchmarkError as exc:
            failure = _failure_record(candidate, manifest, exc.code)
            record_by_index[index] = failure
            if candidate.source is ExtensionSource.BUILTIN:
                _fatal_builtin(tuple(record_by_index.values()))
            continue
        except Exception:
            failure = _failure_record(candidate, manifest, "COMPONENT_REGISTRATION_FAILED")
            record_by_index[index] = failure
            if candidate.source is ExtensionSource.BUILTIN:
                _fatal_builtin(tuple(record_by_index.values()))
            continue

        accepted.append(contribution)
        staged_registries = candidate_registries
        record_by_index[index] = _record(
            candidate, status=ExtensionStatus.LOADED, manifest=manifest
        )

    if staged_registries is None:
        staged_registries = _build_frozen_registries(())
    agents, tools, prompts = staged_registries

    return ExtensionLoadResult(
        records=tuple(record_by_index[index] for index in range(len(candidates))),
        agents=agents,
        tools=tools,
        prompts=prompts,
    )


def load_extensions(settings: ExtensionSettings) -> ExtensionLoadResult:
    """Discover and load extensions using one deterministic transaction."""

    return load_discovered_extensions(discover_extensions(settings), settings)


__all__ = [
    "ExtensionStatus",
    "ExtensionLoadRecord",
    "ExtensionLoadResult",
    "load_discovered_extensions",
    "load_extensions",
]
