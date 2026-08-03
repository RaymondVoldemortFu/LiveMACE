"""Semantic and resource validation for extension directories."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from jsonschema import Draft202012Validator, SchemaError, validators

from benchmark._structured import StructuredDataError, load_structured_file
from benchmark.contracts import ValidationIssue, ValidationReport
from benchmark.prompts import parse_prompt_directory

from .manifest import MANIFEST_FILENAME, ExtensionManifest, _parse_manifest
from .paths import resolve_extension_path


def _issue(path: str, message: str, code: str, validator: str = "") -> ValidationIssue:
    return ValidationIssue(path=path, message=message, validator=validator, code=code)


def _validate_config_schema(
    extension_root: Path,
    relative_path: str,
    issue_path: str,
) -> tuple[ValidationIssue, ...]:
    try:
        schema_path = resolve_extension_path(extension_root, relative_path)
        if schema_path.suffix.lower() not in {".json", ".yaml", ".yml"}:
            raise ValueError("config schema must use .json, .yaml, or .yml")
        raw = load_structured_file(schema_path)
        if not isinstance(raw, Mapping):
            raise ValueError("config schema must be an object")
        validator_type = validators.validator_for(raw, default=Draft202012Validator)
        validator_type.check_schema(raw)
    except StructuredDataError as exc:
        return (_issue(issue_path, str(exc), f"CONFIG_SCHEMA_{exc.code}"),)
    except SchemaError as exc:
        return (
            _issue(
                issue_path,
                exc.message,
                "CONFIG_SCHEMA_INVALID",
                str(exc.validator or ""),
            ),
        )
    except (OSError, ValueError) as exc:
        return (_issue(issue_path, str(exc), "CONFIG_SCHEMA_PATH_INVALID"),)
    return ()


def validate_manifest(
    manifest: ExtensionManifest, extension_root: Path
) -> ValidationReport:
    if not isinstance(manifest, ExtensionManifest):
        raise TypeError("manifest must be ExtensionManifest")
    errors: list[ValidationIssue] = []
    try:
        root = extension_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("extension root must be a directory")
    except (OSError, ValueError) as exc:
        return ValidationReport(
            valid=False,
            errors=(_issue("root", str(exc), "EXTENSION_ROOT_INVALID"),),
        )

    seen_agents: set[str] = set()
    for position, agent in enumerate(manifest.components.agents):
        prefix = f"components.agents.{position}"
        if agent.id in seen_agents:
            errors.append(
                _issue(f"{prefix}.id", "duplicate Agent id", "AGENT_ID_DUPLICATE")
            )
        seen_agents.add(agent.id)
        errors.extend(
            _validate_config_schema(
                root, agent.config_schema, f"{prefix}.config_schema"
            )
        )

    # Prompt ids and profile (id, version) keys must be unique across *all*
    # declared Prompt directories: they end up in one flat registry, where a
    # same-priority conflict would only surface at extension load time.
    # id -> (declaring manifest entry, declared directory)
    seen_prompt_ids: dict[str, tuple[str, str]] = {}
    seen_profile_keys: dict[tuple[str, str], tuple[str, str]] = {}
    for position, prompt in enumerate(manifest.components.prompts):
        prefix = f"components.prompts.{position}"
        try:
            prompt_root = resolve_extension_path(root, prompt.directory)
            if not prompt_root.is_dir():
                raise ValueError("Prompt directory must be a directory")
        except (OSError, ValueError) as exc:
            errors.append(
                _issue(f"{prefix}.directory", str(exc), "PROMPT_DIRECTORY_INVALID")
            )
            continue
        try:
            index_path = resolve_extension_path(root, prompt.index)
            if not index_path.is_relative_to(prompt_root):
                raise ValueError(
                    "Prompt index must be inside its declared Prompt directory"
                )
        except (OSError, ValueError) as exc:
            errors.append(
                _issue(f"{prefix}.index", str(exc), "PROMPT_INDEX_PATH_INVALID")
            )
            continue
        directory_data, prompt_errors = parse_prompt_directory(prompt_root, index_path)
        errors.extend(
            ValidationIssue(
                path=f"{prefix}.{issue.path}" if issue.path else prefix,
                message=issue.message,
                validator=issue.validator,
                code=issue.code,
            )
            for issue in prompt_errors
        )
        if directory_data is None:
            continue
        for prompt_file in directory_data.prompts:
            first_entry, first_directory = seen_prompt_ids.setdefault(
                prompt_file.spec.id, (prefix, prompt.directory)
            )
            if first_entry != prefix:
                errors.append(
                    _issue(
                        f"{prefix}.directory",
                        f"Prompt id {prompt_file.spec.id!r} is declared by both "
                        f"{first_directory!r} and {prompt.directory!r}",
                        "PROMPT_ID_CROSS_DIRECTORY_CONFLICT",
                    )
                )
        for profile in directory_data.profiles:
            key = (profile.id, profile.version)
            first_entry, first_directory = seen_profile_keys.setdefault(
                key, (prefix, prompt.directory)
            )
            if first_entry != prefix:
                errors.append(
                    _issue(
                        f"{prefix}.directory",
                        f"Prompt profile {profile.id!r} version {profile.version!r} "
                        f"is declared by both {first_directory!r} and "
                        f"{prompt.directory!r}",
                        "PROMPT_PROFILE_CROSS_DIRECTORY_CONFLICT",
                    )
                )

    return ValidationReport(valid=not errors, errors=tuple(errors))


def validate_extension_directory(root: Path) -> ValidationReport:
    try:
        extension_root = root.resolve(strict=True)
        if not extension_root.is_dir():
            raise ValueError("extension root must be a directory")
    except (OSError, ValueError) as exc:
        return ValidationReport(
            valid=False,
            errors=(_issue("root", str(exc), "EXTENSION_ROOT_INVALID"),),
        )
    try:
        # Same controlled resolution as every other extension resource: a
        # manifest that is a symlink escaping the extension root is rejected
        # instead of being read from outside the package.
        manifest_path = resolve_extension_path(
            extension_root, MANIFEST_FILENAME, must_exist=False
        )
    except (OSError, ValueError) as exc:
        return ValidationReport(
            valid=False,
            errors=(_issue("manifest", str(exc), "MANIFEST_PATH_INVALID"),),
        )
    manifest, parse_errors = _parse_manifest(manifest_path)
    if manifest is None:
        return ValidationReport(valid=False, errors=parse_errors)
    return validate_manifest(manifest, extension_root)


__all__ = ["validate_manifest", "validate_extension_directory"]
