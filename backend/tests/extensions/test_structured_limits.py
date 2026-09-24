"""Depth limits must hold at parse/compose time, never as a bare
RecursionError (code-review P2)."""

from __future__ import annotations

import pytest

from benchmark._structured import (
    DEFAULT_MAX_DEPTH,
    StructuredDataError,
    load_structured_text,
)


def _nested_flow_sequence(levels: int) -> str:
    return "[" * levels + "]" * levels


def test_yaml_depth_at_the_limit_is_accepted():
    value = load_structured_text(
        _nested_flow_sequence(DEFAULT_MAX_DEPTH), format_name="yaml"
    )
    node = value
    for _ in range(DEFAULT_MAX_DEPTH - 1):
        node = node[0]
    assert node == []


@pytest.mark.parametrize("levels", [DEFAULT_MAX_DEPTH + 1, 500])
def test_deep_yaml_fails_with_stable_error_not_recursion_error(levels):
    with pytest.raises(StructuredDataError) as caught:
        load_structured_text(_nested_flow_sequence(levels), format_name="yaml")
    assert caught.value.code == "NESTING_TOO_DEEP"


def test_deep_yaml_mapping_fails_with_stable_error():
    lines = []
    for depth in range(DEFAULT_MAX_DEPTH + 5):
        lines.append("  " * depth + f"k{depth}:")
    text = "\n".join(lines) + " leaf\n"
    with pytest.raises(StructuredDataError) as caught:
        load_structured_text(text, format_name="yaml")
    assert caught.value.code == "NESTING_TOO_DEEP"


def test_json_depth_at_the_limit_is_accepted():
    assert load_structured_text(
        _nested_flow_sequence(DEFAULT_MAX_DEPTH), format_name="json"
    ) is not None


@pytest.mark.parametrize("levels", [DEFAULT_MAX_DEPTH + 1, 500])
def test_deep_json_fails_with_stable_error(levels):
    with pytest.raises(StructuredDataError) as caught:
        load_structured_text(_nested_flow_sequence(levels), format_name="json")
    assert caught.value.code == "NESTING_TOO_DEEP"


def test_extremely_deep_json_recursion_is_reported_as_stable_error():
    # Deep enough to blow the C decoder's recursion guard before our own
    # post-construction depth walk could run.
    with pytest.raises(StructuredDataError) as caught:
        load_structured_text(_nested_flow_sequence(100_000), format_name="json")
    assert caught.value.code == "NESTING_TOO_DEEP"
