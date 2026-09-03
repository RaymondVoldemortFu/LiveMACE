from __future__ import annotations

from hashlib import sha256

from benchmark.builtin.prompts import (
    PROFILE_CONTRACTS,
    get_builtin_prompt_registry,
    validate_profile_contract,
)
from config.agent_config import AgentConfig
from services.agent.prompts.advanced_multi_agent_prompts import (
    ADVANCED_EXECUTION_PROMPT,
    ANALYST_AGENT_PROMPT,
    CODER_AGENT_PROMPT as ADVANCED_CODER_AGENT_PROMPT,
    CRITIC_AGENT_PROMPT,
    NEWS_AGENT_PROMPT as ADVANCED_NEWS_AGENT_PROMPT,
    TRADING_AGENT_PROMPT as ADVANCED_TRADING_AGENT_PROMPT,
    Advanced_MANAGER_PROMPT,
)
from services.agent.prompts.multi_agent_prompts import (
    CODER_AGENT_PROMPT,
    MANAGER_PROMPT,
    NEWS_AGENT_PROMPT,
    TRADING_AGENT_PROMPT,
)
from services.agent.prompts.sub_agent_prompts import SUB_AGENT_SYSTEM_PROMPT
from services.agent.prompts.system_prompts import get_trade_agent_prompt
from services.agent.rule_aware.llm_auditor import AUDIT_SYSTEM_PROMPT
from services.agent.rule_aware.prompts import (
    RULE_AWARE_REMINDER_PROMPT,
    RULE_AWARE_SYSTEM_PROMPT,
)

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

NON_REACT_PROMPT_GOLDEN_HASHES = {
    "multi.manager": "b3cf8dc36b96afb99fdb997a03a9f03df4cf3972757c100e7e77604533534f72",
    "multi.trading": "2c7837bb28c91d98ad4d0c61ab21becf97d53a7572cf66831c1f94e0ca724a4f",
    "multi.news": "3c9725068cc0eade6efd9dc97e2a85331d9453c76a0cf999039de0d99aa7c80d",
    "multi.coder": "b822d1b698a6ca9db15f86b6e42b00025d05d7814ba8d7cfa286329c9ddab9f3",
    "advanced.manager": "4e5501c65cd35746c64ff9c82ae890c8425a4d0246b8db0766ca12d22462e92c",
    "advanced.execution": "fc38e673db73a778c1bad2a8595cb961eb170524b75f5d1764da88eaed0cd44e",
    "advanced.trading": "4f646d4f99f1dfc12cf5da65ff7d12385a9d928d7f62c99b31804da02677dc7a",
    "advanced.news": "2c226fab0db89396d469c216d6c8fb371f886de1a1da98dd5055e22c5e07ee06",
    "advanced.coder": "cf6b772dbda8247212ccf1dc1fa205638f3ae3bf606093a8ce8e475762c2ce40",
    "advanced.analyst": "37d2af51bd8a49a02853c197da599be85efb8abac7d55abfddb6dbe95b86c3d5",
    "advanced.critic": "a9dae0a5555b82c9e657606e65b05d1bb72a6f840f095e9466e31dccb7cb10a2",
    "rule-aware.system": "3a551aca4ba18602461669413b2df9c86c345edf8e6088dae50e02c2dcaf14dc",
    "rule-aware.reminder": "0c9d7a232004a40bc4af6d9a5b2e21d455a019aa4c799984294befc39c49fd70",
    "search.system": "ba9c27d6f5cbfdb32aa51e820e16b5bb57319ee7b9000d9684b8d1af864b64fe",
    "compliance-audit.system": "8ffb977e126e9a726a35983fc1cc0eb71e8e45d1bfc6f927f43c39aec6cc09c8",
}

NON_REACT_PROMPTS = {
    "multi.manager": MANAGER_PROMPT,
    "multi.trading": TRADING_AGENT_PROMPT,
    "multi.news": NEWS_AGENT_PROMPT,
    "multi.coder": CODER_AGENT_PROMPT,
    "advanced.manager": Advanced_MANAGER_PROMPT,
    "advanced.execution": ADVANCED_EXECUTION_PROMPT,
    "advanced.trading": ADVANCED_TRADING_AGENT_PROMPT,
    "advanced.news": ADVANCED_NEWS_AGENT_PROMPT,
    "advanced.coder": ADVANCED_CODER_AGENT_PROMPT,
    "advanced.analyst": ANALYST_AGENT_PROMPT,
    "advanced.critic": CRITIC_AGENT_PROMPT,
    "rule-aware.system": RULE_AWARE_SYSTEM_PROMPT,
    "rule-aware.reminder": RULE_AWARE_REMINDER_PROMPT,
    "search.system": SUB_AGENT_SYSTEM_PROMPT,
    "compliance-audit.system": AUDIT_SYSTEM_PROMPT,
}


def test_react_prompt_variants_match_pre_migration_hashes(monkeypatch):
    for (notice, memory, routing), expected in REACT_GOLDEN_HASHES.items():
        monkeypatch.setattr(AgentConfig, "AGENT_INCLUDE_SIMULATION_NOTICE", notice)
        content = get_trade_agent_prompt(memory, routing)
        assert sha256(content.encode("utf-8")).hexdigest() == expected


def test_non_react_prompts_match_pre_migration_hashes():
    assert NON_REACT_PROMPTS.keys() == NON_REACT_PROMPT_GOLDEN_HASHES.keys()
    for prompt_name, content in NON_REACT_PROMPTS.items():
        expected = NON_REACT_PROMPT_GOLDEN_HASHES[prompt_name]
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
