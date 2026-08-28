"""Deprecated compatibility facade over the public Agent registry.

New extensions register through :mod:`benchmark.agents`.  This module keeps
the old ``create_agent(agent_type, llm, tools, **kwargs)`` API stable until
M10 switches production callers to ``AgentRuntime``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
from typing import Any

from benchmark.agents import (
    AgentBuildContext,
    AgentDescriptor,
    AgentRegistry,
    ComponentNotFoundError,
    NullEventSink,
)
from benchmark.builtin.agents import register_builtin_agents
from benchmark.builtin.agents.advanced_multi_agent import (
    ADVANCED_MULTI_AGENT_COMPONENT_ID,
    ADVANCED_MULTI_AGENT_SHIM_CONFIG_KEYS,
)
from benchmark.builtin.agents.react import REACT_COMPONENT_ID, REACT_SHIM_CONFIG_KEYS
from benchmark.builtin.agents.rule_aware import (
    RULE_AWARE_COMPONENT_ID,
    RULE_AWARE_SHIM_CONFIG_KEYS,
)
from benchmark.builtin.prompts import get_builtin_prompt_registry
from benchmark.infrastructure.adapters import LegacyLLMClientAdapter

from .base import BaseAgent
from .llm_client import LLMClient
from .multi_agent import MultiAgent
from .tools import ToolRegistry

logger = logging.getLogger(__name__)

_BUILTIN_PROMPT_REGISTRY = get_builtin_prompt_registry()


class _LegacyAgentFactory:
    def __init__(self, builder: Callable[..., BaseAgent]) -> None:
        self._builder = builder

    def create(
        self,
        context: AgentBuildContext,
        config: Mapping[str, Any],
    ) -> BaseAgent:
        llm = context.llm
        if isinstance(llm, LegacyLLMClientAdapter):
            llm = llm.legacy_client
        return self._builder(llm, context.tools, **dict(config))


def _build_multi_agent(llm: LLMClient, tools: ToolRegistry, **config: Any) -> BaseAgent:
    return MultiAgent(
        llm,
        tools,
        max_steps=config.get("max_steps", 15),
        user_id=config.get("user_id"),
        agent_name=config.get("agent_name"),
    )


def _legacy_schema(default_max_steps: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "max_steps": {"type": "integer", "minimum": 1, "default": default_max_steps},
            "user_id": {"type": ["string", "integer", "null"]},
            "account_id": {"type": ["integer", "null"]},
            "agent_name": {"type": ["string", "null"]},
            "rule_docs_path": {"type": ["string", "null"]},
            "enable_llm_audit": {"type": "boolean"},
        },
        # Legacy callers have historically supplied implementation-specific kwargs.
        "additionalProperties": True,
    }


def _create_legacy_registry() -> AgentRegistry:
    registry = AgentRegistry()
    register_builtin_agents(registry)
    builtins = (
        ("core.multi-agent", 15, _build_multi_agent),
    )
    for agent_id, default_max_steps, builder in builtins:
        registry.register(
            AgentDescriptor(
                id=agent_id,
                version="1.0.0",
                config_schema=_legacy_schema(default_max_steps),
                display_name=agent_id.removeprefix("core."),
                description="Built-in Agent exposed through the legacy compatibility facade.",
            ),
            _LegacyAgentFactory(builder),
        )
    registry.freeze()
    return registry


_LEGACY_AGENT_IDS = {
    "react": "core.react",
    "default": "core.react",
    "multi_agent": "core.multi-agent",
    "rule_aware": "core.rule-aware",
    "advanced_multi_agent": "core.advanced-multi-agent",
}
_LEGACY_REGISTRY = _create_legacy_registry()


_SHIM_CONFIG_KEYS = {
    REACT_COMPONENT_ID: REACT_SHIM_CONFIG_KEYS,
    ADVANCED_MULTI_AGENT_COMPONENT_ID: ADVANCED_MULTI_AGENT_SHIM_CONFIG_KEYS,
    RULE_AWARE_COMPONENT_ID: RULE_AWARE_SHIM_CONFIG_KEYS,
}


def _shim_config(agent_id: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    allowed_keys = _SHIM_CONFIG_KEYS.get(agent_id)
    if allowed_keys is None:
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in allowed_keys}


def create_agent(
    agent_type: str,
    llm: LLMClient,
    tools: ToolRegistry,
    **kwargs: Any,
) -> BaseAgent:
    """Create a built-in legacy Agent through a registry-backed facade."""

    normalized_type = str(agent_type or "react").strip().lower()
    agent_id = _LEGACY_AGENT_IDS.get(normalized_type, normalized_type)
    if "." not in agent_id:
        agent_id = f"core.{agent_id.replace('_', '-')}"
    try:
        registered = _LEGACY_REGISTRY.get(agent_id)
    except ComponentNotFoundError as exc:
        raise ValueError(f"Unknown agent type: {agent_type}") from exc

    config = _shim_config(agent_id, kwargs)
    report = _LEGACY_REGISTRY.validate_config(agent_id, config)
    if not report.valid:
        message = "; ".join(
            f"{issue.path or '<root>'}: {issue.message}" for issue in report.errors
        )
        raise ValueError(f"Invalid config for agent type {agent_type}: {message}")
    created = registered.factory.create(
        AgentBuildContext(
            llm=LegacyLLMClientAdapter(llm),
            tools=tools,
            prompts=_BUILTIN_PROMPT_REGISTRY,
            events=NullEventSink(),
        ),
        report.normalized_config,
    )
    if isinstance(created, BaseAgent):
        return created
    legacy = getattr(created, "legacy_agent", None)
    if isinstance(legacy, BaseAgent):
        return legacy
    raise TypeError(
        f"factory for {agent_id} did not return a BaseAgent or an adapter with legacy_agent"
    )


__all__ = ["create_agent"]
