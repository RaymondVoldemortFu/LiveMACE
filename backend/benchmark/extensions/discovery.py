"""Deterministic discovery of declared benchmark extension roots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import Iterable, Mapping

from benchmark.contracts import KNOWN_CAPABILITIES, require_identifier

from .manifest import MANIFEST_FILENAME


class ExtensionSource(str, Enum):
    BUILTIN = "builtin"
    EXTERNAL = "external"


@dataclass(frozen=True)
class ExtensionSettings:
    """Inputs needed by discovery and loading before runtime integration."""

    builtin_root: Path | None = None
    extension_roots: tuple[Path, ...] = ()
    disabled_extensions: frozenset[str] = frozenset()
    allowed_capabilities: frozenset[str] = frozenset(KNOWN_CAPABILITIES)

    def __post_init__(self) -> None:
        if self.builtin_root is not None and not isinstance(self.builtin_root, Path):
            raise TypeError("builtin_root must be Path or None")
        roots = tuple(self.extension_roots)
        if not all(isinstance(root, Path) for root in roots):
            raise TypeError("extension_roots must contain Path values")
        disabled = frozenset(self.disabled_extensions)
        for extension_id in disabled:
            require_identifier(extension_id, "disabled extension id")
        capabilities = frozenset(self.allowed_capabilities)
        unknown = capabilities.difference(KNOWN_CAPABILITIES)
        if unknown:
            raise ValueError(
                "allowed_capabilities contains unknown values: "
                + ", ".join(sorted(unknown))
            )
        object.__setattr__(self, "extension_roots", roots)
        object.__setattr__(self, "disabled_extensions", disabled)
        object.__setattr__(self, "allowed_capabilities", capabilities)


@dataclass(frozen=True)
class ExtensionCandidate:
    """A direct extension root selected in deterministic discovery order."""

    root: Path
    manifest_path: Path
    source: ExtensionSource

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path) or not isinstance(self.manifest_path, Path):
            raise TypeError("extension candidate paths must be Path values")
        if not isinstance(self.source, ExtensionSource):
            raise TypeError("source must be ExtensionSource")


def _normalized_root(root: Path) -> Path:
    return root.expanduser().resolve(strict=False)


def _root_key(root: Path) -> str:
    return os.path.normcase(str(root))


def _root_sort_key(root: Path) -> tuple[str, str]:
    root_text = str(root)
    return os.path.normcase(root_text), root_text


def _candidate(root: Path, source: ExtensionSource) -> ExtensionCandidate:
    normalized = _normalized_root(root)
    return ExtensionCandidate(normalized, normalized / MANIFEST_FILENAME, source)


def discover_extensions(settings: ExtensionSettings) -> tuple[ExtensionCandidate, ...]:
    """Return builtin-first, path-sorted candidates without recursive scanning."""

    if not isinstance(settings, ExtensionSettings):
        raise TypeError("settings must be ExtensionSettings")

    candidates: list[ExtensionCandidate] = []
    seen_roots: set[str] = set()

    def add(root: Path, source: ExtensionSource) -> None:
        candidate = _candidate(root, source)
        key = _root_key(candidate.root)
        if key in seen_roots:
            return
        seen_roots.add(key)
        candidates.append(candidate)

    if settings.builtin_root is not None:
        add(settings.builtin_root, ExtensionSource.BUILTIN)

    external_roots = sorted(
        (_normalized_root(root) for root in settings.extension_roots),
        key=_root_sort_key,
    )
    for root in external_roots:
        add(root, ExtensionSource.EXTERNAL)
    return tuple(candidates)


def settings_from_environ(
    environ: Mapping[str, str] | None = None,
) -> ExtensionSettings:
    """Build discovery settings from the ALPHA_ARENA_* process environment."""

    source = os.environ if environ is None else environ
    directories = source.get("ALPHA_ARENA_EXTENSION_DIRS", "")
    roots = tuple(
        Path(item.strip())
        for item in directories.split(":")
        if item.strip()
    )
    disabled = frozenset(
        item.strip()
        for item in source.get("ALPHA_ARENA_DISABLED_EXTENSIONS", "").split(",")
        if item.strip()
    )
    raw_capabilities = source.get("ALPHA_ARENA_ALLOWED_CAPABILITIES")
    if raw_capabilities is None or not raw_capabilities.strip():
        capabilities = KNOWN_CAPABILITIES
    else:
        capabilities = frozenset(
            item.strip() for item in raw_capabilities.split(",") if item.strip()
        )
    return ExtensionSettings(
        extension_roots=roots,
        disabled_extensions=disabled,
        allowed_capabilities=capabilities,
    )


def settings_from_paths(
    *,
    builtin_root: Path | None = None,
    extension_roots: Iterable[Path] = (),
    disabled_extensions: Iterable[str] = (),
    allowed_capabilities: Iterable[str] = KNOWN_CAPABILITIES,
) -> ExtensionSettings:
    """Small convenience constructor used by tests and future bootstrap code."""

    return ExtensionSettings(
        builtin_root=builtin_root,
        extension_roots=tuple(extension_roots),
        disabled_extensions=frozenset(disabled_extensions),
        allowed_capabilities=frozenset(allowed_capabilities),
    )


__all__ = [
    "ExtensionSource",
    "ExtensionSettings",
    "ExtensionCandidate",
    "discover_extensions",
    "settings_from_environ",
    "settings_from_paths",
]
