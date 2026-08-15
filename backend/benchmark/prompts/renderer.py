"""Strict, code-free named-placeholder rendering for Prompt files."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from string import Formatter
from typing import Mapping

from benchmark.contracts import (
    JsonValue,
    PromptRenderError,
    PromptSpec,
    RenderedPrompt,
    require_variable_name,
    to_jsonable,
)

MAX_RENDERED_CHARACTERS = 2_000_000


@dataclass(frozen=True)
class ParsedTemplate:
    content: str
    variables: frozenset[str]


def parse_template(content: str) -> ParsedTemplate:
    if not isinstance(content, str):
        raise TypeError("content must be a string")
    variables: set[str] = set()
    try:
        fields = Formatter().parse(content)
        for _literal, field_name, format_spec, conversion in fields:
            if field_name is None:
                continue
            if not field_name:
                raise ValueError("positional placeholders are not supported")
            require_variable_name(field_name, "placeholder")
            if format_spec:
                raise ValueError("format specifications are not supported")
            if conversion:
                raise ValueError("placeholder conversions are not supported")
            variables.add(field_name)
    except (ValueError, TypeError) as exc:
        raise PromptRenderError(
            "prompt template syntax is invalid",
            code="PROMPT_TEMPLATE_INVALID",
            details={"reason": str(exc)},
        ) from exc
    return ParsedTemplate(content=content, variables=frozenset(variables))


def validate_template_variables(template: ParsedTemplate, spec: PromptSpec) -> None:
    declared = set(spec.required_variables).union(spec.optional_variables)
    if template.variables != declared:
        raise PromptRenderError(
            "prompt template variables do not match its specification",
            code="PROMPT_VARIABLE_DECLARATION_MISMATCH",
            details={
                "prompt_id": spec.id,
                "missing_in_template": sorted(declared - template.variables),
                "undeclared_in_template": sorted(template.variables - declared),
            },
        )


def _serialize_value(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(
            to_jsonable(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PromptRenderError(
            "prompt variable is not a valid JSON value",
            code="PROMPT_VARIABLE_INVALID",
        ) from exc


def render_template(
    template: ParsedTemplate,
    spec: PromptSpec,
    variables: Mapping[str, JsonValue],
    *,
    max_characters: int = MAX_RENDERED_CHARACTERS,
) -> RenderedPrompt:
    if not isinstance(variables, Mapping):
        raise TypeError("variables must be a mapping")
    provided = set(variables)
    if not all(isinstance(name, str) for name in provided):
        raise PromptRenderError(
            "prompt variable names must be strings",
            code="PROMPT_VARIABLE_INVALID",
        )
    allowed = set(spec.required_variables).union(spec.optional_variables)
    missing = set(spec.required_variables) - provided
    unknown = provided - allowed
    if missing or unknown:
        raise PromptRenderError(
            "prompt variables are incomplete or contain unknown names",
            code="PROMPT_VARIABLES_INVALID",
            details={"missing": sorted(missing), "unknown": sorted(unknown)},
        )

    merged: dict[str, JsonValue] = dict(spec.optional_variables)
    merged.update(variables)
    serialized = {name: _serialize_value(value) for name, value in merged.items()}

    # Templates forbid format specs and conversions, so the rendered length
    # is exactly the literal text plus every placeholder's serialized value.
    # Enforce the limit *before* allocating the rendered string so oversized
    # renders fail without materializing a huge result.
    projected_length = 0
    for literal_text, field_name, _format_spec, _conversion in Formatter().parse(
        template.content
    ):
        projected_length += len(literal_text)
        if field_name is not None:
            projected_length += len(serialized[field_name])
        if projected_length > max_characters:
            raise PromptRenderError(
                "rendered prompt exceeds the character limit",
                code="PROMPT_RENDER_TOO_LARGE",
                details={
                    "max_characters": max_characters,
                    "projected_characters_at_least": projected_length,
                },
            )

    content = template.content.format_map(serialized)
    digest = sha256(content.encode("utf-8")).hexdigest()
    return RenderedPrompt(spec=spec, content=content, content_sha256=digest)


__all__ = [
    "MAX_RENDERED_CHARACTERS",
    "ParsedTemplate",
    "parse_template",
    "validate_template_variables",
    "render_template",
]
