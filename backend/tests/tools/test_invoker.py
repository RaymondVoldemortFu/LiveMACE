from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import get_ident

import pytest

from benchmark.contracts import (
    MEMORY_WRITE,
    NETWORK_READ,
    SideEffect,
    ToolResult,
    ToolRuntimeError,
)
from benchmark.tools import SynchronousToolInvoker, ToolRegistry

from .conftest import FunctionTool, MemoryCache, Provider, make_spec


def registered(extension, tool, **registry_kwargs):
    registry = ToolRegistry(**registry_kwargs)
    registry.register_provider(extension, Provider(tool))
    registry.freeze()
    return registry


def invoker(registry, *, events=None, **kwargs):
    return SynchronousToolInvoker(
        registry,
        account_id=7,
        decision_round_id=kwargs.pop("decision_round_id", "round-7"),
        trace_id="trace-7",
        events=events,
        call_id_factory=lambda: "call-7",
        **kwargs,
    )


def test_tool_runs_synchronously_in_caller_thread_and_receives_context(
    extension, events
):
    thread_ids = []

    def run(context, arguments):
        thread_ids.append(get_ident())
        assert context.account_id == 7
        assert context.call_id == "call-7"
        return ToolResult(ok=True, value=arguments["value"] + 1)

    tool = FunctionTool(make_spec(), run)
    runtime = invoker(registered(extension, tool), events=events)
    caller = get_ident()

    result = runtime.call("com.example.echo", {"value": 2})

    assert result == ToolResult(ok=True, value=3)
    assert thread_ids == [caller]
    assert [event.type for event in events.events] == [
        "tool.started",
        "tool.completed",
    ]


def test_capability_and_input_schema_fail_before_invoke(extension, events):
    tool = FunctionTool(
        make_spec(
            "com.example.search",
            side_effect=SideEffect.EXTERNAL_READ,
            capabilities=(NETWORK_READ,),
        ),
        lambda context, arguments: ToolResult(ok=True, value=1),
    )
    registry = registered(extension, tool)

    denied = invoker(registry, events=events).call("com.example.search", {"value": 1})
    invalid = invoker(
        registry,
        capabilities=frozenset({NETWORK_READ}),
    ).call("com.example.search", {"value": "bad"})

    assert denied.error_code == "TOOL_CAPABILITY_DENIED"
    assert invalid.error_code == "TOOL_INPUT_INVALID"
    assert tool.calls == []
    assert events.events[-1].type == "tool.denied"


def test_active_selection_hides_disabled_tool(extension):
    tool = FunctionTool(
        make_spec(),
        lambda context, arguments: ToolResult(ok=True, value=1),
    )
    registry = registered(extension, tool)
    result = invoker(registry, enabled_tools=()).call("com.example.echo", {"value": 1})
    assert result.error_code == "TOOL_NOT_ACTIVE"
    assert tool.calls == []


def test_business_failure_is_returned_but_framework_failures_raise(extension):
    business = FunctionTool(
        make_spec(),
        lambda context, arguments: ToolResult(
            ok=False,
            error_code="PROVIDER_BUSY",
            error_message="retry later",
            retryable=True,
        ),
    )
    assert (
        invoker(registered(extension, business))
        .call("com.example.echo", {"value": 1})
        .error_code
        == "PROVIDER_BUSY"
    )

    broken = FunctionTool(
        make_spec("com.example.broken"),
        lambda context, arguments: (_ for _ in ()).throw(
            RuntimeError("provider secret must not leak")
        ),
    )
    with pytest.raises(ToolRuntimeError) as caught:
        invoker(registered(extension, broken)).call("com.example.broken", {"value": 1})
    assert caught.value.code == "TOOL_INVOKE_FAILED"
    assert "provider secret" not in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_awaitable_and_bad_output_are_rejected(extension):
    async def asynchronous(context, arguments):
        return ToolResult(ok=True, value=1)

    async_tool = FunctionTool(make_spec(), asynchronous)
    with pytest.raises(ToolRuntimeError) as caught:
        invoker(registered(extension, async_tool)).call(
            "com.example.echo", {"value": 1}
        )
    assert caught.value.code == "ASYNC_TOOL_UNSUPPORTED"

    bad_output = FunctionTool(
        make_spec("com.example.bad-output"),
        lambda context, arguments: ToolResult(ok=True, value="not-an-integer"),
    )
    with pytest.raises(ToolRuntimeError) as caught:
        invoker(registered(extension, bad_output)).call(
            "com.example.bad-output", {"value": 1}
        )
    assert caught.value.code == "TOOL_OUTPUT_INVALID"


def test_elapsed_timeout_returns_stable_failure(extension, events):
    ticks = iter((10.0, 12.0))
    tool = FunctionTool(
        make_spec(timeout=1),
        lambda context, arguments: ToolResult(ok=True, value=1),
    )
    result = invoker(
        registered(extension, tool),
        events=events,
        monotonic_clock=lambda: next(ticks),
    ).call("com.example.echo", {"value": 1})

    assert result.error_code == "TOOL_TIMEOUT"
    assert result.retryable is True
    assert events.events[-1].type == "tool.failed"


def test_elapsed_timeout_preserves_completed_write_result(extension, events):
    ticks = iter((10.0, 12.0))
    tool = FunctionTool(
        make_spec(
            "com.example.memory-write",
            side_effect=SideEffect.MEMORY_WRITE,
            capabilities=(MEMORY_WRITE,),
            timeout=1,
        ),
        lambda context, arguments: ToolResult(ok=True, value=arguments["value"]),
    )
    result = invoker(
        registered(extension, tool),
        capabilities=frozenset({MEMORY_WRITE}),
        events=events,
        monotonic_clock=lambda: next(ticks),
    ).call("com.example.memory-write", {"value": 1})

    assert result.ok is True
    assert result.value == 1
    assert result.error_code is None
    assert result.metadata["timeout_exceeded"] is True
    assert result.metadata["timeout_seconds"] == 1.0
    assert result.metadata["elapsed_seconds"] == 2.0
    assert [event.type for event in events.events] == [
        "tool.started",
        "tool.completed",
    ]


def test_expired_deadline_fails_before_invoke(extension, events):
    now = datetime(2026, 7, 31, tzinfo=timezone.utc)
    tool = FunctionTool(
        make_spec(),
        lambda context, arguments: ToolResult(ok=True, value=1),
    )
    result = invoker(
        registered(extension, tool),
        events=events,
        deadline_at=now - timedelta(seconds=1),
        clock=lambda: now,
    ).call("com.example.echo", {"value": 1})

    assert result.error_code == "TOOL_DEADLINE_EXCEEDED"
    assert result.retryable is True
    assert tool.calls == []
    assert events.events[-1].type == "tool.failed"


def test_cache_key_includes_extension_version_arguments_and_round(extension, events):
    cache = MemoryCache()
    tool = FunctionTool(
        make_spec(cacheable=True),
        lambda context, arguments: ToolResult(ok=True, value=arguments["value"]),
    )
    registry = registered(extension, tool)
    first = invoker(registry, cache=cache, events=events)

    assert first.call("com.example.echo", {"value": 1}).value == 1
    assert first.call("com.example.echo", {"value": 1}).value == 1
    assert len(tool.calls) == 1
    assert cache.sets[0][0] == "com.example.extension@1.2.3:com.example.echo"
    assert cache.sets[0][-1] == "round-7"
    assert [event.type for event in events.events] == [
        "tool.started",
        "tool.completed",
        "tool.cache_hit",
    ]

    second_round = invoker(registry, cache=cache, decision_round_id="round-8")
    second_round.call("com.example.echo", {"value": 1})
    first.call("com.example.echo", {"value": 2})
    assert len(tool.calls) == 3


def test_invalid_cached_output_is_treated_as_a_miss(extension):
    cache = MemoryCache()
    tool = FunctionTool(
        make_spec(cacheable=True),
        lambda context, arguments: ToolResult(ok=True, value=arguments["value"]),
    )
    runtime = invoker(registered(extension, tool), cache=cache)
    runtime.call("com.example.echo", {"value": 1})
    cached_key = next(iter(cache.values))
    cache.values[cached_key]["value"] = "corrupt"

    result = runtime.call("com.example.echo", {"value": 1})

    assert result.value == 1
    assert len(tool.calls) == 2


def test_only_read_only_cacheable_tools_use_cache(extension):
    cache = MemoryCache()
    tool = FunctionTool(
        make_spec(
            "com.example.search",
            side_effect=SideEffect.EXTERNAL_READ,
            capabilities=(NETWORK_READ,),
            cacheable=True,
        ),
        lambda context, arguments: ToolResult(ok=True, value=arguments["value"]),
    )
    runtime = invoker(
        registered(extension, tool),
        capabilities=frozenset({NETWORK_READ}),
        cache=cache,
    )
    runtime.call("com.example.search", {"value": 1})
    runtime.call("com.example.search", {"value": 1})
    assert len(tool.calls) == 2
    assert cache.gets == []
    assert cache.sets == []


def test_events_redact_credentials_without_changing_tool_result(extension, events):
    tool = FunctionTool(
        make_spec(
            input_schema={
                "type": "object",
                "properties": {"api_key": {"type": "string"}},
                "required": ["api_key"],
            },
            output_schema={
                "type": "object",
                "properties": {"token": {"type": "string"}},
                "required": ["token"],
            },
        ),
        lambda context, arguments: ToolResult(
            ok=True,
            value={"token": arguments["api_key"]},
        ),
    )
    result = invoker(registered(extension, tool), events=events).call(
        "com.example.echo", {"api_key": "super-secret"}
    )

    assert result.value == {"token": "super-secret"}
    serialized_events = repr(events.events)
    assert "super-secret" not in serialized_events
    assert "[REDACTED]" in serialized_events
