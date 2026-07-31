"""Internal bounded JSON/YAML parsing shared by extension resource loaders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken

DEFAULT_MAX_BYTES = 1024 * 1024
DEFAULT_MAX_ALIASES = 50
DEFAULT_MAX_DEPTH = 32


class StructuredDataError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class _UniqueKeySafeLoader(yaml.SafeLoader):
    def construct_mapping(
        self, node: MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise StructuredDataError(
                    "mapping keys must be scalar values",
                    code="NON_SCALAR_KEY",
                ) from exc
            if duplicate:
                raise StructuredDataError(
                    f"duplicate mapping key: {key!r}",
                    code="DUPLICATE_KEY",
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def read_limited_text(path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> str:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise StructuredDataError(
            "file is not readable", code="FILE_UNREADABLE"
        ) from exc
    if size > max_bytes:
        raise StructuredDataError(
            f"file exceeds the {max_bytes} byte limit",
            code="FILE_TOO_LARGE",
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise StructuredDataError(
            "file is not readable", code="FILE_UNREADABLE"
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StructuredDataError("file must be UTF-8", code="INVALID_UTF8") from exc
    if text.startswith("\ufeff"):
        raise StructuredDataError(
            "UTF-8 BOM is not supported", code="UTF8_BOM_UNSUPPORTED"
        )
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _reject_duplicate_json(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StructuredDataError(
                f"duplicate mapping key: {key!r}",
                code="DUPLICATE_KEY",
            )
        result[key] = value
    return result


def _check_json_value_and_depth(value: Any, *, max_depth: int) -> None:
    active: set[int] = set()
    heights: dict[int, int] = {}

    def visit(item: Any, depth: int) -> int:
        if depth > max_depth:
            raise StructuredDataError(
                f"document exceeds nesting depth {max_depth}",
                code="NESTING_TOO_DEEP",
            )
        if isinstance(item, Mapping):
            identity = id(item)
            cached_height = heights.get(identity)
            if cached_height is not None:
                if depth + cached_height - 1 > max_depth:
                    raise StructuredDataError(
                        f"document exceeds nesting depth {max_depth}",
                        code="NESTING_TOO_DEEP",
                    )
                return cached_height
            if identity in active:
                raise StructuredDataError(
                    "recursive aliases are not supported", code="RECURSIVE_ALIAS"
                )
            active.add(identity)
            try:
                child_heights = []
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise StructuredDataError(
                            "mapping keys must be strings",
                            code="NON_STRING_KEY",
                        )
                    child_heights.append(visit(child, depth + 1))
            finally:
                active.remove(identity)
            height = 1 + max(child_heights, default=0)
            heights[identity] = height
            return height
        elif isinstance(item, Sequence) and not isinstance(
            item, (str, bytes, bytearray)
        ):
            identity = id(item)
            cached_height = heights.get(identity)
            if cached_height is not None:
                if depth + cached_height - 1 > max_depth:
                    raise StructuredDataError(
                        f"document exceeds nesting depth {max_depth}",
                        code="NESTING_TOO_DEEP",
                    )
                return cached_height
            if identity in active:
                raise StructuredDataError(
                    "recursive aliases are not supported", code="RECURSIVE_ALIAS"
                )
            active.add(identity)
            try:
                child_heights = [visit(child, depth + 1) for child in item]
            finally:
                active.remove(identity)
            height = 1 + max(child_heights, default=0)
            heights[identity] = height
            return height
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise StructuredDataError(
                    "non-finite numbers are not supported",
                    code="NON_FINITE_NUMBER",
                )
        elif item is not None and not isinstance(item, (str, bool, int)):
            raise StructuredDataError(
                "document contains a non-JSON value",
                code="NON_JSON_VALUE",
            )
        return 1

    visit(value, 1)


def load_structured_text(
    text: str,
    *,
    format_name: str,
    max_aliases: int = DEFAULT_MAX_ALIASES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> Any:
    try:
        if format_name == "json":
            value = json.loads(text, object_pairs_hook=_reject_duplicate_json)
        elif format_name in {"yaml", "yml"}:
            aliases = sum(isinstance(token, AliasToken) for token in yaml.scan(text))
            if aliases > max_aliases:
                raise StructuredDataError(
                    f"document exceeds alias limit {max_aliases}",
                    code="TOO_MANY_ALIASES",
                )
            value = yaml.load(text, Loader=_UniqueKeySafeLoader)
        else:
            raise StructuredDataError(
                "unsupported structured file format", code="UNSUPPORTED_FORMAT"
            )
    except StructuredDataError:
        raise
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise StructuredDataError(
            "file contains invalid structured data", code="PARSE_ERROR"
        ) from exc
    _check_json_value_and_depth(value, max_depth=max_depth)
    return value


def load_structured_file(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_aliases: int = DEFAULT_MAX_ALIASES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> Any:
    suffix = path.suffix.lower().lstrip(".")
    text = read_limited_text(path, max_bytes=max_bytes)
    return load_structured_text(
        text,
        format_name=suffix,
        max_aliases=max_aliases,
        max_depth=max_depth,
    )


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_ALIASES",
    "DEFAULT_MAX_DEPTH",
    "StructuredDataError",
    "read_limited_text",
    "load_structured_text",
    "load_structured_file",
]
