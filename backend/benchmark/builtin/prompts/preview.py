"""Preview helpers for account system-prompt APIs."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from benchmark.accounts.config import config_from_legacy_account
from benchmark.builtin.prompts import (
    get_builtin_prompt_registry,
    render_react_prompt,
    render_template_source,
)
from config.agent_config import AgentConfig


@dataclass(frozen=True)
class SystemPromptPreview:
    agent_id: str
    system_prompt: str
    prompt_profile_id: str | None
    prompt_profile_version: str | None
    prompt_id: str | None
    prompt_version: str | None
    prompt_hash: str


def _hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _slot_identity(profile_id: str, slot: str) -> tuple[str, str, str]:
    resolver = get_builtin_prompt_registry()
    profile = resolver.get_profile(profile_id)
    selection = profile.slots[slot]
    spec = resolver.get_prompt_spec(selection.prompt_id, version=selection.version)
    return profile.version, spec.id, spec.version


def preview_system_prompt_for_account(account: Any) -> SystemPromptPreview:
    config = config_from_legacy_account(account)
    agent_id = config.agent_id
    profile_id = config.prompt_profile_id

    if agent_id in {"baseline.buy-hold", "baseline.grid"}:
        content = (
            "This account uses a baseline strategy and does not rely on an LLM system prompt "
            "for decision generation."
        )
        return SystemPromptPreview(
            agent_id=agent_id,
            system_prompt=content,
            prompt_profile_id=None,
            prompt_profile_version=None,
            prompt_id=None,
            prompt_version=None,
            prompt_hash=_hash(content),
        )

    if profile_id is None:
        content = f"Unknown agent_id={agent_id!r}. No dedicated system prompt template found."
        return SystemPromptPreview(
            agent_id=agent_id,
            system_prompt=content,
            prompt_profile_id=None,
            prompt_profile_version=None,
            prompt_id=None,
            prompt_version=None,
            prompt_hash=_hash(content),
        )

    if agent_id == "core.react":
        memory_enabled = bool(config.agent_config.get("memory_enabled"))
        tool_routing_enabled = bool(config.agent_config.get("tool_routing_enabled"))
        content = render_react_prompt(
            memory_enabled=memory_enabled,
            tool_routing_enabled=tool_routing_enabled,
            include_simulation_notice=bool(
                getattr(AgentConfig, "AGENT_INCLUDE_SIMULATION_NOTICE", False)
            ),
        )
        slot = "system"
    elif agent_id == "core.multi-agent":
        content = render_template_source("core.multi-agent.manager")
        slot = "manager"
    elif agent_id == "core.advanced-multi-agent":
        content = render_template_source("core.advanced-multi-agent.manager")
        slot = "manager"
    elif agent_id == "core.rule-aware":
        content = render_template_source("core.rule-aware.system")
        slot = "system"
    else:
        content = f"Unknown agent_id={agent_id!r}. No dedicated system prompt template found."
        return SystemPromptPreview(
            agent_id=agent_id,
            system_prompt=content,
            prompt_profile_id=profile_id,
            prompt_profile_version=None,
            prompt_id=None,
            prompt_version=None,
            prompt_hash=_hash(content),
        )

    profile_version, prompt_id, prompt_version = _slot_identity(profile_id, slot)
    return SystemPromptPreview(
        agent_id=agent_id,
        system_prompt=content,
        prompt_profile_id=profile_id,
        prompt_profile_version=profile_version,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        prompt_hash=_hash(content),
    )


__all__ = ["SystemPromptPreview", "preview_system_prompt_for_account"]
