"""Extension-root path containment helpers."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


def resolve_extension_path(root: Path, value: str, *, must_exist: bool = True) -> Path:
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
        raise ValueError("path resolves outside the extension root")
    return candidate


__all__ = ["resolve_extension_path"]
