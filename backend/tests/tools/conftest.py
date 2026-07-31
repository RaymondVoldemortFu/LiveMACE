from __future__ import annotations

from copy import deepcopy

import pytest

from benchmark.contracts import ExtensionRef, SideEffect, ToolResult, ToolSpec


class FunctionTool:
    def __init__(self, spec, function):
        self._spec = spec
        self.function = function
        self.calls = []

    @property
    def spec(self):
        return self._spec

    def invoke(self, context, arguments):
        self.calls.append((context, arguments))
        return self.function(context, arguments)


class Provider:
    def __init__(self, *tools):
        self.tools = tools

    def list_tools(self):
        return self.tools


class RecordingEvents:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class MemoryCache:
    def __init__(self):
        self.values = {}
        self.gets = []
        self.sets = []

    @staticmethod
    def _key(namespace, arguments, round_id):
        normalized = tuple(
            sorted((key, repr(value)) for key, value in arguments.items())
        )
        return namespace, normalized, round_id

    def get(self, namespace, args, *, round_id=None):
        self.gets.append((namespace, deepcopy(args), round_id))
        return deepcopy(self.values.get(self._key(namespace, args, round_id)))

    def set(self, namespace, args, value, *, ttl_seconds=None, round_id=None):
        self.sets.append(
            (namespace, deepcopy(args), deepcopy(value), ttl_seconds, round_id)
        )
        self.values[self._key(namespace, args, round_id)] = deepcopy(value)


def make_spec(
    name="com.example.echo",
    *,
    side_effect=SideEffect.READ_ONLY,
    capabilities=(),
    cacheable=False,
    timeout=30.0,
    input_schema=None,
    output_schema=None,
):
    return ToolSpec(
        name=name,
        description="M05 contract Tool",
        input_schema=input_schema
        or {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema=output_schema or {"type": "integer"},
        side_effect=side_effect,
        timeout_seconds=timeout,
        cacheable=cacheable,
        required_capabilities=tuple(capabilities),
    )


@pytest.fixture
def extension():
    return ExtensionRef("com.example.extension", "1.2.3")


@pytest.fixture
def events():
    return RecordingEvents()


@pytest.fixture
def echo_tool():
    return FunctionTool(
        make_spec(),
        lambda context, arguments: ToolResult(ok=True, value=arguments["value"]),
    )
