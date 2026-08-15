"""Static Prompt directory and index validation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from benchmark._structured import (
    StructuredDataError,
    load_structured_file,
    read_limited_text,
)
from benchmark.contracts import (
    PromptProfileDescriptor,
    PromptRenderError,
    PromptSelection,
    PromptSpec,
    ValidationIssue,
    ValidationReport,
)

from .renderer import ParsedTemplate, parse_template, validate_template_variables

PROMPT_FILE_MAX_BYTES = 1024 * 1024
_SCHEMA_PATH = Path(__file__).with_name("schema") / "index-v1.json"


@dataclass(frozen=True)
class ValidatedPromptFile:
    spec: PromptSpec
    template: ParsedTemplate


@dataclass(frozen=True)
class PromptDirectoryData:
    prompts: tuple[ValidatedPromptFile, ...]
    profiles: tuple[PromptProfileDescriptor, ...]


def resolve_contained_path(root: Path, value: str, *, must_exist: bool = True) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a non-empty string")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        raise ValueError("path must be relative and must not contain '..'")
    root_resolved = root.resolve(strict=True)
    candidate = (root_resolved / Path(value)).resolve(strict=must_exist)
    if not candidate.is_relative_to(root_resolved):
        raise ValueError("path resolves outside the Prompt root")
    return candidate


def _json_path(parts: Any) -> str:
    return ".".join(str(part) for part in parts)


def _schema() -> Mapping[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _issue(path: str, message: str, code: str, validator: str = "") -> ValidationIssue:
    return ValidationIssue(path=path, message=message, validator=validator, code=code)


def _parse_prompt_directory(
    root: Path, index: Path
) -> tuple[PromptDirectoryData | None, tuple[ValidationIssue, ...]]:
    errors: list[ValidationIssue] = []
    try:
        prompt_root = root.resolve(strict=True)
        if not prompt_root.is_dir():
            raise ValueError("Prompt root must be a directory")
    except (OSError, ValueError) as exc:
        return None, (_issue("root", str(exc), "PROMPT_ROOT_INVALID"),)

    try:
        index_path = index.resolve(strict=True)
        if not index_path.is_relative_to(prompt_root):
            raise ValueError("Prompt index must be inside the Prompt root")
    except (OSError, ValueError) as exc:
        return None, (_issue("index", str(exc), "PROMPT_INDEX_PATH_INVALID"),)

    try:
        raw = load_structured_file(index_path)
    except StructuredDataError as exc:
        return None, (_issue("index", str(exc), f"PROMPT_INDEX_{exc.code}"),)
    if not isinstance(raw, Mapping):
        return None, (
            _issue(
                "index", "Prompt index must be an object", "PROMPT_INDEX_TYPE_INVALID"
            ),
        )

    validator = Draft202012Validator(_schema())
    for error in validator.iter_errors(raw):
        errors.append(
            _issue(
                _json_path(error.absolute_path),
                error.message,
                "PROMPT_INDEX_SCHEMA_INVALID",
                str(error.validator or ""),
            )
        )
    if errors:
        return None, tuple(errors)

    prompt_files: list[ValidatedPromptFile] = []
    seen_prompt_ids: set[str] = set()
    for position, item in enumerate(raw["prompts"]):
        path_prefix = f"prompts.{position}"
        try:
            spec = PromptSpec(
                id=item["id"],
                version=item["version"],
                required_variables=tuple(item.get("required_variables", ())),
                optional_variables=item.get("optional_variables", {}),
                content_type=item.get("content_type", "text/plain"),
            )
            if spec.id in seen_prompt_ids:
                raise ValueError(
                    "a Prompt directory may contain only one version of each Prompt id"
                )
            seen_prompt_ids.add(spec.id)
        except (TypeError, ValueError) as exc:
            errors.append(_issue(path_prefix, str(exc), "PROMPT_SPEC_INVALID"))
            continue
        try:
            file_path = resolve_contained_path(prompt_root, item["file"])
            if file_path.suffix.lower() not in {".txt", ".md"}:
                raise ValueError("Prompt files must use .txt or .md")
            content = read_limited_text(file_path, max_bytes=PROMPT_FILE_MAX_BYTES)
            template = parse_template(content)
            validate_template_variables(template, spec)
            prompt_files.append(ValidatedPromptFile(spec=spec, template=template))
        except StructuredDataError as exc:
            reason = exc.code.removeprefix("FILE_")
            errors.append(
                _issue(f"{path_prefix}.file", str(exc), f"PROMPT_FILE_{reason}")
            )
        except (OSError, ValueError) as exc:
            errors.append(
                _issue(f"{path_prefix}.file", str(exc), "PROMPT_FILE_PATH_INVALID")
            )
        except PromptRenderError as exc:
            errors.append(_issue(path_prefix, exc.message, exc.code))

    profiles: list[PromptProfileDescriptor] = []
    seen_profiles: set[tuple[str, str]] = set()
    for position, item in enumerate(raw.get("profiles", ())):
        path_prefix = f"profiles.{position}"
        try:
            slots = {
                slot: PromptSelection(selection["prompt_id"], selection.get("version"))
                for slot, selection in item["slots"].items()
            }
            profile = PromptProfileDescriptor(item["id"], item["version"], slots)
            key = (profile.id, profile.version)
            if key in seen_profiles:
                raise ValueError("duplicate Prompt profile id and version")
            seen_profiles.add(key)
            profiles.append(profile)
        except (TypeError, ValueError, KeyError) as exc:
            errors.append(_issue(path_prefix, str(exc), "PROMPT_PROFILE_INVALID"))

    if errors:
        return None, tuple(errors)
    return PromptDirectoryData(tuple(prompt_files), tuple(profiles)), ()


def validate_prompt_directory(root: Path, index: Path) -> ValidationReport:
    _data, errors = _parse_prompt_directory(root, index)
    return ValidationReport(valid=not errors, errors=errors)


def parse_prompt_directory(
    root: Path, index: Path
) -> tuple[PromptDirectoryData | None, tuple[ValidationIssue, ...]]:
    """Validate a Prompt directory and also return its parsed contents.

    Used by extension validation to aggregate Prompt ids and profile keys
    across all declared Prompt directories.
    """
    return _parse_prompt_directory(root, index)


__all__ = [
    "PROMPT_FILE_MAX_BYTES",
    "ValidatedPromptFile",
    "PromptDirectoryData",
    "resolve_contained_path",
    "parse_prompt_directory",
    "validate_prompt_directory",
]
