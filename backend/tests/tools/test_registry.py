from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from benchmark.contracts import (
    ACCOUNT_READ,
    NETWORK_READ,
    TRADING_WRITE,
    ComponentConfigError,
    ComponentConflictError,
    ExtensionRef,
    SideEffect,
    ToolResult,
)
from benchmark.tools import (
    ToolRegistry,
    ToolRegistryFrozenError,
)

from .conftest import FunctionTool, Provider, make_spec


def test_third_party_provider_registers_and_openai_schema_uses_canonical_input(
    extension, echo_tool
):
    registry = ToolRegistry()
    registry.register_provider(extension, Provider(echo_tool))
    view = registry.view(frozenset())

    assert registry.get("com.example.echo").extension == extension
    assert registry.list() == (echo_tool.spec,)
    assert view.openai_tools[0]["function"]["parameters"] == {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }


def test_duplicate_names_fail_atomically(extension, echo_tool):
    registry = ToolRegistry()
    with pytest.raises(ComponentConflictError) as caught:
        registry.register_provider(extension, Provider(echo_tool, echo_tool))
    assert caught.value.code == "TOOL_PROVIDER_NAME_CONFLICT"
    assert registry.list() == ()

    registry.register_provider(extension, Provider(echo_tool))
    other = FunctionTool(
        echo_tool.spec, lambda context, arguments: ToolResult(ok=True, value=1)
    )
    with pytest.raises(ComponentConflictError) as caught:
        registry.register_provider(
            ExtensionRef("com.other.extension", "2.0.0"), Provider(other)
        )
    assert caught.value.code == "TOOL_NAME_CONFLICT"


def test_freeze_blocks_registration_and_allows_concurrent_reads(extension, echo_tool):
    registry = ToolRegistry()
    registry.register_provider(extension, Provider(echo_tool))
    registry.freeze()
    registry.freeze()

    with pytest.raises(ToolRegistryFrozenError):
        registry.register_provider(extension, Provider(echo_tool))
    with ThreadPoolExecutor(max_workers=8) as executor:
        names = list(executor.map(lambda _: registry.list()[0].name, range(100)))
    assert names == ["com.example.echo"] * 100


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf"), True])
def test_tool_spec_rejects_non_finite_or_boolean_timeout(timeout):
    with pytest.raises(ValueError, match="positive finite"):
        make_spec(timeout=timeout)


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        (make_spec(name="Bad.Name"), "TOOL_NAME_INVALID"),
        (make_spec(timeout=301), "TOOL_TIMEOUT_INVALID"),
        (
            make_spec(input_schema={"type": "not-a-type"}),
            "TOOL_SCHEMA_INVALID",
        ),
        (
            make_spec(
                side_effect=SideEffect.MEMORY_WRITE,
                capabilities=(),
            ),
            "TOOL_CAPABILITY_POLICY_INVALID",
        ),
        (
            make_spec(
                side_effect=SideEffect.READ_ONLY,
                capabilities=(TRADING_WRITE,),
            ),
            "TOOL_CAPABILITY_POLICY_INVALID",
        ),
    ],
)
def test_spec_policy_validation_is_stable(extension, spec, code):
    registry = ToolRegistry()
    tool = FunctionTool(spec, lambda context, arguments: ToolResult(ok=True, value=1))
    with pytest.raises(ComponentConfigError) as caught:
        registry.register_provider(extension, Provider(tool))
    assert caught.value.code == code


def test_trading_write_requires_explicit_name_allowlist(extension):
    spec = make_spec(
        "com.example.trade",
        side_effect=SideEffect.TRADING_WRITE,
        capabilities=(TRADING_WRITE,),
    )
    tool = FunctionTool(spec, lambda context, arguments: ToolResult(ok=True, value=1))
    with pytest.raises(ComponentConfigError) as caught:
        ToolRegistry().register_provider(extension, Provider(tool))
    assert caught.value.code == "TOOL_TRADING_CAPABILITY_FORBIDDEN"

    allowed = ToolRegistry(
        trading_write_allowlist=("core.execute_trade", "com.example.trade")
    )
    allowed.register_provider(extension, Provider(tool))
    assert allowed.get("com.example.trade").spec is spec


def test_core_trade_is_invisible_without_trading_capability(extension):
    spec = make_spec(
        "core.execute_trade",
        side_effect=SideEffect.TRADING_WRITE,
        capabilities=(TRADING_WRITE,),
    )
    tool = FunctionTool(spec, lambda context, arguments: ToolResult(ok=True, value=1))
    registry = ToolRegistry()
    registry.register_builtin_provider(
        ExtensionRef("benchmark.core", "1.0.0"), Provider(tool)
    )

    assert registry.list(frozenset()) == ()
    assert registry.view(frozenset()).list() == ()
    with pytest.raises(ComponentConfigError) as caught:
        registry.view(frozenset()).get("core.execute_trade")
    assert caught.value.code == "TOOL_CAPABILITY_DENIED"
    assert registry.view(frozenset({TRADING_WRITE})).list() == (spec,)


@pytest.mark.parametrize(
    "extension",
    [
        ExtensionRef("com.example.extension", "1.0.0"),
        ExtensionRef("benchmark.core", "9.0.0"),
    ],
)
def test_regular_registration_cannot_claim_core_namespace(extension):
    tool = FunctionTool(
        make_spec("core.market_snapshot"),
        lambda context, arguments: ToolResult(ok=True, value=1),
    )
    with pytest.raises(ComponentConfigError) as caught:
        ToolRegistry().register_provider(extension, Provider(tool))
    assert caught.value.code == "TOOL_CORE_NAMESPACE_FORBIDDEN"


def test_builtin_registration_can_claim_core_namespace():
    extension = ExtensionRef("benchmark.core", "1.0.0")
    tool = FunctionTool(
        make_spec("core.market_snapshot"),
        lambda context, arguments: ToolResult(ok=True, value=1),
    )
    registry = ToolRegistry()

    registry.register_builtin_provider(extension, Provider(tool))

    assert registry.get("core.market_snapshot").extension == extension


def test_tool_view_filters_selection_and_capabilities_without_mutating_registry(
    extension,
):
    account_tool = FunctionTool(
        make_spec("com.example.account", capabilities=(ACCOUNT_READ,)),
        lambda context, arguments: ToolResult(ok=True, value=1),
    )
    network_tool = FunctionTool(
        make_spec(
            "com.example.network",
            side_effect=SideEffect.EXTERNAL_READ,
            capabilities=(NETWORK_READ,),
        ),
        lambda context, arguments: ToolResult(ok=True, value=1),
    )
    registry = ToolRegistry()
    registry.register_provider(extension, Provider(account_tool, network_tool))

    account_view = registry.view(
        frozenset({ACCOUNT_READ}),
        enabled_tools=("com.example.account",),
    )
    network_view = registry.view(frozenset({NETWORK_READ}))

    assert [spec.name for spec in account_view.list()] == ["com.example.account"]
    assert [spec.name for spec in network_view.list()] == ["com.example.network"]
    assert len(registry.list()) == 2
