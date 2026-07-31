"""Tool specification, JSON Schema, and OpenAI-schema validation helpers."""

from __future__ import annotations

from collections.abc import Mapping
import copy
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, validators

from benchmark.contracts import JsonValue, TRADING_WRITE, ToolSpec, require_identifier

from .capabilities import capability_policy_errors
from .errors import ComponentConfigError

MAX_TOOL_TIMEOUT_SECONDS = 300.0


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_thaw(item) for item in sorted(value, key=repr)]
    return copy.deepcopy(value)


def schema_validator(schema: Mapping[str, JsonValue]):
    raw_schema = _thaw(schema)
    validator_type = validators.validator_for(
        raw_schema,
        default=Draft202012Validator,
    )
    validator_type.check_schema(raw_schema)
    return validator_type(raw_schema)


def validate_tool_spec(
    spec: ToolSpec,
    *,
    trading_write_allowlist: frozenset[str],
) -> None:
    if not isinstance(spec, ToolSpec):
        raise ComponentConfigError(
            "Tool provider returned an invalid Tool specification",
            code="TOOL_SPEC_INVALID",
        )
    try:
        require_identifier(spec.name, "tool name")
    except ValueError as exc:
        raise ComponentConfigError(
            "Tool name must be a lowercase ASCII namespaced identifier",
            code="TOOL_NAME_INVALID",
            details={"tool_name": spec.name},
        ) from exc
    if float(spec.timeout_seconds) > MAX_TOOL_TIMEOUT_SECONDS:
        raise ComponentConfigError(
            f"Tool timeout may not exceed {MAX_TOOL_TIMEOUT_SECONDS:g} seconds",
            code="TOOL_TIMEOUT_INVALID",
            details={"tool_name": spec.name},
        )
    try:
        schema_validator(spec.input_schema)
        schema_validator(spec.output_schema)
    except SchemaError as exc:
        raise ComponentConfigError(
            "Tool JSON Schema is invalid",
            code="TOOL_SCHEMA_INVALID",
            details={"tool_name": spec.name, "message": exc.message},
        ) from exc
    policy_errors = capability_policy_errors(spec)
    if policy_errors:
        raise ComponentConfigError(
            "Tool side effect and capability declarations are inconsistent",
            code="TOOL_CAPABILITY_POLICY_INVALID",
            details={"tool_name": spec.name, "errors": list(policy_errors)},
        )
    if TRADING_WRITE in spec.required_capabilities and spec.name not in (
        trading_write_allowlist
    ):
        raise ComponentConfigError(
            "Tool is not authorized to request trading.write",
            code="TOOL_TRADING_CAPABILITY_FORBIDDEN",
            details={"tool_name": spec.name},
        )


def validation_messages(
    schema: Mapping[str, JsonValue],
    instance: object,
) -> tuple[dict[str, str], ...]:
    validator = schema_validator(schema)
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda item: (
            tuple(str(part) for part in item.absolute_path),
            item.message,
        ),
    )
    return tuple(
        {
            "path": ".".join(str(part) for part in error.absolute_path),
            "message": error.message,
            "validator": str(error.validator or ""),
        }
        for error in errors
    )


def openai_tool_schema(spec: ToolSpec) -> dict[str, JsonValue]:
    """Generate the model-facing schema from the canonical input schema."""

    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": _thaw(spec.input_schema),
        },
    }


__all__ = [
    "MAX_TOOL_TIMEOUT_SECONDS",
    "openai_tool_schema",
    "schema_validator",
    "validate_tool_spec",
    "validation_messages",
]
