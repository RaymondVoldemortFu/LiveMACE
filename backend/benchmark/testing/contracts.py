"""Public contract helpers for extension authors."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any, Mapping

from benchmark.agents import AgentBuildContext, AgentFactory
from benchmark.contracts import (
    TRADING_WRITE,
    AgentRunResult,
    DecisionContext,
    PromptSpec,
    RenderedPrompt,
    ToolResult,
)
from benchmark.prompts import PromptProvider
from benchmark.testing.context import (
    build_fake_build_context,
    build_fake_context,
    tool_context_from_decision,
)
from benchmark.tools import ToolProvider


class ContractViolation(AssertionError):
    """Raised when an extension violates the public synchronous SPI."""


def _reject_awaitable(value: Any, message: str) -> Any:
    if isawaitable(value):
        close = getattr(value, "close", None)
        if callable(close):
            close()
        raise ContractViolation(message)
    return value


@dataclass(frozen=True)
class AgentCase:
    name: str
    config: Mapping[str, Any] = field(default_factory=dict)
    context: DecisionContext | None = None
    build_context: AgentBuildContext | None = None


@dataclass(frozen=True)
class ToolCase:
    name: str
    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    context: Any = None


def assert_agent_contract(factory: AgentFactory, cases: Sequence[AgentCase]) -> None:
    if not callable(getattr(factory, "create", None)):
        raise ContractViolation("Agent factory must implement create()")
    if not cases:
        raise ContractViolation("Agent contract requires at least one case")
    for case in cases:
        build_context = case.build_context or build_fake_build_context()
        created = _reject_awaitable(
            factory.create(build_context, dict(case.config)),
            f"{case.name}: AgentFactory.create() returned an awaitable",
        )
        run = getattr(created, "run", None)
        if not callable(run):
            raise ContractViolation(f"{case.name}: factory did not return an Agent")
        result = _reject_awaitable(
            run(case.context or build_fake_context()),
            f"{case.name}: Agent.run() returned an awaitable",
        )
        if not isinstance(result, AgentRunResult):
            raise ContractViolation(
                f"{case.name}: Agent.run() must return AgentRunResult"
            )


def assert_tool_contract(provider: ToolProvider, cases: Sequence[ToolCase] = ()) -> None:
    if not callable(getattr(provider, "list_tools", None)):
        raise ContractViolation("Tool provider must implement list_tools()")
    listed = _reject_awaitable(
        provider.list_tools(),
        "ToolProvider.list_tools() returned an awaitable",
    )
    tools = tuple(listed)
    if not tools:
        raise ContractViolation("Tool provider must expose at least one Tool")
    by_name = {}
    for tool in tools:
        spec = getattr(tool, "spec", None)
        if spec is None or not callable(getattr(tool, "invoke", None)):
            raise ContractViolation("Tool provider returned an invalid Tool")
        if TRADING_WRITE in spec.required_capabilities and spec.name != "core.execute_trade":
            raise ContractViolation(
                f"{spec.name} requested trading.write without being core.execute_trade"
            )
        by_name[spec.name] = tool
    for case in cases:
        tool = by_name.get(case.tool_name)
        if tool is None:
            raise ContractViolation(f"{case.name}: Tool {case.tool_name!r} is not provided")
        if case.context is None:
            context = tool_context_from_decision(build_fake_context())
        elif isinstance(case.context, DecisionContext):
            context = tool_context_from_decision(case.context)
        else:
            context = case.context
        result = _reject_awaitable(
            tool.invoke(context, dict(case.arguments)),
            f"{case.name}: Tool.invoke() returned an awaitable",
        )
        if not isinstance(result, ToolResult):
            raise ContractViolation(f"{case.name}: Tool.invoke() must return ToolResult")


def assert_prompt_contract(provider: PromptProvider) -> None:
    if not callable(getattr(provider, "list_prompts", None)):
        raise ContractViolation("Prompt provider must implement list_prompts()")
    listed = _reject_awaitable(
        provider.list_prompts(),
        "PromptProvider.list_prompts() returned an awaitable",
    )
    specs = tuple(listed)
    if not specs:
        raise ContractViolation("Prompt provider must expose at least one Prompt")
    for spec in specs:
        if not isinstance(spec, PromptSpec):
            raise ContractViolation("Prompt provider returned an invalid PromptSpec")
        variables = {
            name: "{" + name + "}"
            for name in (*spec.required_variables, *spec.optional_variables)
        }
        rendered = _reject_awaitable(
            provider.render(spec.id, variables),
            f"{spec.id}: PromptProvider.render() returned an awaitable",
        )
        if not isinstance(rendered, RenderedPrompt):
            raise ContractViolation(
                f"{spec.id}: PromptProvider.render() must return RenderedPrompt"
            )


__all__ = [
    "AgentCase",
    "ToolCase",
    "ContractViolation",
    "assert_agent_contract",
    "assert_tool_contract",
    "assert_prompt_contract",
]
