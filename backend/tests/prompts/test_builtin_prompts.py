from __future__ import annotations

from hashlib import sha256

from benchmark.builtin.prompts import (
    PROFILE_CONTRACTS,
    get_builtin_prompt_registry,
    validate_profile_contract,
)
from config.agent_config import AgentConfig
from services.agent.prompts.system_prompts import get_trade_agent_prompt

REACT_GOLDEN_HASHES = {
    (
        False,
        False,
        False,
    ): "69d53e95b0f4e5bafc2d005690c29ecb9face0f933b51cf0d5ae7423d5197993",
    (
        False,
        False,
        True,
    ): "2cfd59d3be7c2e520463cb1cc835335c8d1476921063bee7cc6f8f3f61fc7d43",
    (
        False,
        True,
        False,
    ): "9c5b1db7957979e8adea8d599ab270b70e446800c09e106a09ad6942ab80e189",
    (
        False,
        True,
        True,
    ): "76faf9afa7b8c97a8bb0f5e71441f75d755320254c5c828e42b2ddd1fb491de0",
    (
        True,
        False,
        False,
    ): "70c5f03c4b45730bf44c7e2c00cdd50e54bd08802fa7b5b7fd6b7577e6544a24",
    (
        True,
        False,
        True,
    ): "5f9aa3d9b0c880512581ffbcb3cf904666676aee2bdb49a2508f73dd7aae4fe3",
    (
        True,
        True,
        False,
    ): "98f2118d26555ea7eb352c569d4a373d66192870c161385d9ed03147dc469ce2",
    (
        True,
        True,
        True,
    ): "25dd8f9a37803f74efcb7757c5fabbb4f25fd3c333280420a040b9153a04b6d1",
}


def test_react_prompt_variants_match_pre_migration_hashes(monkeypatch):
    for (notice, memory, routing), expected in REACT_GOLDEN_HASHES.items():
        monkeypatch.setattr(AgentConfig, "AGENT_INCLUDE_SIMULATION_NOTICE", notice)
        content = get_trade_agent_prompt(memory, routing)
        assert sha256(content.encode("utf-8")).hexdigest() == expected


def test_builtin_profiles_match_agent_slot_contracts():
    resolver = get_builtin_prompt_registry()
    cases = {
        "core.react.default": "react",
        "core.react.memory": "react",
        "core.react.tool-routing": "react",
        "core.react.memory-tool-routing": "react",
        "core.multi-agent.default": "multi_agent",
        "core.advanced-multi-agent.default": "advanced_multi_agent",
        "core.rule-aware.default": "rule_aware",
        "core.search-sub-agent.default": "search_sub_agent",
        "core.compliance-audit.default": "compliance_audit",
    }
    assert set(PROFILE_CONTRACTS) == set(cases.values())
    for profile_id, contract in cases.items():
        assert validate_profile_contract(resolver, profile_id, contract).valid
