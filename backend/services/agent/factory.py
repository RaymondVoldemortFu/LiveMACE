"""Deprecated compatibility facade over the public Agent registry.

New extensions register through :mod:`benchmark.agents`.  This module keeps
the old ``create_agent(agent_type, llm, tools, **kwargs)`` API stable until the
built-in Agent adapters are migrated in M04.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
import os
from typing import Any

from benchmark.agents import (
    AgentBuildContext,
    AgentDescriptor,
    AgentRegistry,
    ComponentNotFoundError,
    NullEventSink,
)
from config.agent_config import AgentConfig

from .base import BaseAgent
from .llm_client import LLMClient
from .multi_agent import MultiAgent
from .react import ReActAgent
from .tools import ToolRegistry


logger = logging.getLogger(__name__)


class _LegacyAgentFactory:
    def __init__(self, builder: Callable[..., BaseAgent]) -> None:
        self._builder = builder

    def create(
        self,
        context: AgentBuildContext,
        config: Mapping[str, Any],
    ) -> BaseAgent:
        return self._builder(context.llm, context.tools, **dict(config))


def _build_react(llm: LLMClient, tools: ToolRegistry, **config: Any) -> BaseAgent:
    return ReActAgent(
        llm,
        tools,
        max_steps=config.get("max_steps", AgentConfig.MAX_STEPS),
        user_id=config.get("user_id"),
        agent_name=config.get("agent_name"),
    )


def _build_multi_agent(llm: LLMClient, tools: ToolRegistry, **config: Any) -> BaseAgent:
    return MultiAgent(
        llm,
        tools,
        max_steps=config.get("max_steps", 15),
        user_id=config.get("user_id"),
        agent_name=config.get("agent_name"),
    )


def _build_rule_aware(llm: LLMClient, tools: ToolRegistry, **config: Any) -> BaseAgent:
    from .rule_aware import RuleAwareAgent, RuleEngine

    rule_docs_path = config.get("rule_docs_path")
    if not rule_docs_path:
        rule_docs_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "config",
            "rules",
        )
    rule_engine = RuleEngine(rule_docs_path)
    logger.info("Loaded %s rules for rule-aware agent", rule_engine.get_rule_summary())
    return RuleAwareAgent(
        llm,
        tools,
        rule_engine,
        max_steps=config.get("max_steps", AgentConfig.MAX_STEPS),
        user_id=config.get("user_id"),
        account_id=config.get("account_id"),
        enable_llm_audit=config.get("enable_llm_audit", False),
        agent_name=config.get("agent_name"),
    )


def _build_advanced_multi_agent(
    llm: LLMClient,
    tools: ToolRegistry,
    **config: Any,
) -> BaseAgent:
    from .multi_agent_advanced import AdvancedMultiAgent

    return AdvancedMultiAgent(
        llm,
        tools,
        max_steps=config.get("max_steps", 30),
        user_id=config.get("user_id"),
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
    builtins = (
        ("core.react", AgentConfig.MAX_STEPS, _build_react),
        ("core.multi-agent", 15, _build_multi_agent),
        ("core.rule-aware", AgentConfig.MAX_STEPS, _build_rule_aware),
        ("core.advanced-multi-agent", 30, _build_advanced_multi_agent),
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

    report = _LEGACY_REGISTRY.validate_config(agent_id, kwargs)
    if not report.valid:
        message = "; ".join(
            f"{issue.path or '<root>'}: {issue.message}" for issue in report.errors
        )
        raise ValueError(f"Invalid config for agent type {agent_type}: {message}")
    return registered.factory.create(
        AgentBuildContext(
            llm=llm,
            tools=tools,
            prompts=None,
            events=NullEventSink(),
        ),
        report.normalized_config,
    )


__all__ = ["create_agent"]
