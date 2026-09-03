"""Account system-prompt preview returns profile identity and hash."""

from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

from benchmark.builtin.prompts import render_template_source
from benchmark.builtin.prompts.preview import preview_system_prompt_for_account
from services.agent.prompts.system_prompts import get_trade_agent_prompt


def _account(**flags):
    return SimpleNamespace(
        agent_type=flags.get("agent_type", "react"),
        memory_enabled=flags.get("memory_enabled", "false"),
        tool_routing_enabled=flags.get("tool_routing_enabled", "true"),
        enable_rule_aware=flags.get("enable_rule_aware", "false"),
    )


def test_react_preview_returns_profile_id_version_and_hash():
    preview = preview_system_prompt_for_account(
        _account(memory_enabled="false", tool_routing_enabled="true")
    )
    expected = get_trade_agent_prompt(memory_enabled=False, tool_routing_enabled=True)
    assert preview.agent_id == "core.react"
    assert preview.prompt_profile_id == "core.react.tool-routing"
    assert preview.prompt_profile_version == "1.0.0"
    assert preview.prompt_id == "core.react.system"
    assert preview.prompt_version == "1.0.0"
    assert preview.system_prompt == expected
    assert preview.prompt_hash == sha256(expected.encode("utf-8")).hexdigest()


def test_multi_agent_preview_uses_manager_template():
    preview = preview_system_prompt_for_account(_account(agent_type="multi_agent"))
    expected = render_template_source("core.multi-agent.manager")
    assert preview.agent_id == "core.multi-agent"
    assert preview.prompt_profile_id == "core.multi-agent.default"
    assert preview.prompt_id == "core.multi-agent.manager"
    assert preview.system_prompt == expected
    assert preview.prompt_hash == sha256(expected.encode("utf-8")).hexdigest()


def test_advanced_and_rule_aware_preview_return_profile_identity():
    advanced = preview_system_prompt_for_account(
        _account(agent_type="advanced_multi_agent")
    )
    expected_advanced = render_template_source("core.advanced-multi-agent.manager")
    assert advanced.agent_id == "core.advanced-multi-agent"
    assert advanced.prompt_profile_id == "core.advanced-multi-agent.default"
    assert advanced.prompt_id == "core.advanced-multi-agent.manager"
    assert advanced.system_prompt == expected_advanced
    assert advanced.prompt_hash == sha256(expected_advanced.encode("utf-8")).hexdigest()

    rule_aware = preview_system_prompt_for_account(
        _account(enable_rule_aware="true")
    )
    expected_rule = render_template_source("core.rule-aware.system")
    assert rule_aware.agent_id == "core.rule-aware"
    assert rule_aware.prompt_profile_id == "core.rule-aware.default"
    assert rule_aware.prompt_id == "core.rule-aware.system"
    assert rule_aware.system_prompt == expected_rule
    assert rule_aware.prompt_hash == sha256(expected_rule.encode("utf-8")).hexdigest()


def test_baseline_preview_has_hash_without_prompt_profile():
    preview = preview_system_prompt_for_account(_account(agent_type="buy_hold"))
    assert preview.agent_id == "baseline.buy-hold"
    assert preview.prompt_profile_id is None
    assert preview.prompt_id is None
    assert preview.prompt_hash == sha256(preview.system_prompt.encode("utf-8")).hexdigest()
