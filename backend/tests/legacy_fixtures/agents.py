"""Registry constructor for legacy-agent characterization tests."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from benchmark.agents import (
    AgentBuildContext,
    AgentRegistry,
    ComponentNotFoundError,
    NullEventSink,
)
from benchmark.builtin.agents import register_builtin_agents
from benchmark.builtin.agents.advanced_multi_agent import (
    ADVANCED_MULTI_AGENT_COMPONENT_ID,
    ADVANCED_MULTI_AGENT_SHIM_CONFIG_KEYS,
)
from benchmark.builtin.agents.multi_agent import (
    MULTI_AGENT_COMPONENT_ID,
    MULTI_AGENT_SHIM_CONFIG_KEYS,
)
from benchmark.builtin.agents.react import REACT_COMPONENT_ID, REACT_SHIM_CONFIG_KEYS
from benchmark.builtin.agents.rule_aware import (
    RULE_AWARE_COMPONENT_ID,
    RULE_AWARE_SHIM_CONFIG_KEYS,
)
from benchmark.builtin.prompts import get_builtin_prompt_registry
from benchmark.infrastructure.adapters import LegacyLLMClientAdapter

from services.agent.base import BaseAgent
from services.agent.llm_client import LLMClient
from services.agent.tools import ToolRegistry

logger = logging.getLogger(__name__)

_BUILTIN_PROMPT_REGISTRY = get_builtin_prompt_registry()


def _create_legacy_registry() -> AgentRegistry:
    registry = AgentRegistry()
    register_builtin_agents(registry)
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
    MULTI_AGENT_COMPONENT_ID: MULTI_AGENT_SHIM_CONFIG_KEYS,
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
