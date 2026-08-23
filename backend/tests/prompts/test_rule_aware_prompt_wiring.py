from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from hashlib import sha256
import json

from benchmark.builtin.prompts import get_builtin_prompt_registry
from services.agent.rule_aware import rule_aware_agent as rule_aware_module
from services.agent.rule_aware.llm_auditor import LLMAuditor
from services.agent.rule_aware.rule_aware_agent import RuleAwareAgent
from services.agent.rule_aware.rule_engine import RuleEngine
from services.agent.tools import ToolRegistry
from tests.fakes import FakeLLM, FakeLLMResponse

RULE_DOCUMENTS = "## R0\nR0-01: Keep leverage <= 2"
PORTFOLIO = {
    "account_id": 7,
    "cash": 10000.0,
    "total_equity": 12000.0,
    "positions": {"BTC": {"quantity": 0.1}},
}
PRICES = {"BTC": 50000.0, "AAPL": 200.5}
RULE_SYSTEM_GOLDEN_HASH = (
    "e5242e5a67d53ebfb14e04fd83167e6a66c464acbf409a895c3c9ad73060835c"
)
RULE_REMINDER_GOLDEN_HASH = (
    "6212b0828cfc705107148a9585a04bf96cddc1a91628ed02806af529647aa7c4"
)
AUDIT_MESSAGES_GOLDEN_HASH = (
    "19c085801e50eb6d1b0e7ff58c2b1ad3d8b7b333c3f6086090df069cc1a637e5"
)


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 8, 16, 12, 0, 0, tzinfo=tz)


class _OverrideResolver:
    def __init__(self, overrides):
        self._delegate = get_builtin_prompt_registry()
        self._overrides = dict(overrides)
        self.calls = []

    def get_prompt_spec(self, prompt_id, *, version=None):
        return self._delegate.get_prompt_spec(prompt_id, version=version)

    def get_profile(self, profile_id, *, version=None):
        return self._delegate.get_profile(profile_id, version=version)

    def render(self, prompt_id, variables):
        return self._delegate.render(prompt_id, variables)

    def render_slot(
        self,
        profile_id,
        slot,
        variables,
        *,
        profile_version=None,
    ):
        self.calls.append((profile_id, slot, dict(variables)))
        rendered = self._delegate.render_slot(
            profile_id,
            slot,
            variables,
            profile_version=profile_version,
        )
        content = self._overrides.get((profile_id, slot))
        if content is None:
            return rendered
        return replace(
            rendered,
            content=content,
            content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        )


def _rule_engine() -> RuleEngine:
    engine = RuleEngine()
    engine.format_rules_for_prompt = lambda: RULE_DOCUMENTS
    return engine


def _audit_response() -> str:
    return json.dumps(
        {
            "coverage": {"score": 8, "reason": "ok"},
            "conflict": {"score": 7, "reason": "ok"},
            "final_normalized_score": 0.75,
        }
    )


def test_rule_agent_default_messages_match_pre_migration_hashes(monkeypatch):
    monkeypatch.setattr(rule_aware_module, "datetime", _FixedDateTime)
    llm = FakeLLM(
        [FakeLLMResponse("still thinking"), FakeLLMResponse("still thinking")]
    )
    agent = RuleAwareAgent(
        llm,
        ToolRegistry(),
        _rule_engine(),
        max_steps=2,
    )

    agent.run(PORTFOLIO, PRICES)

    messages = llm.calls[0]["messages"]
    assert [message["role"] for message in messages] == ["system", "user", "user"]
    assert sha256(messages[0]["content"].encode("utf-8")).hexdigest() == (
        RULE_SYSTEM_GOLDEN_HASH
    )
    assert sha256(messages[2]["content"].encode("utf-8")).hexdigest() == (
        RULE_REMINDER_GOLDEN_HASH
    )


def test_rule_agent_uses_injected_profile_slots(monkeypatch):
    monkeypatch.setattr(rule_aware_module, "datetime", _FixedDateTime)
    resolver = _OverrideResolver(
        {
            ("core.rule-aware.default", "system"): "custom rule system",
            ("core.rule-aware.default", "reminder"): "custom rule reminder",
        }
    )
    llm = FakeLLM(
        [FakeLLMResponse("still thinking"), FakeLLMResponse("still thinking")]
    )
    agent = RuleAwareAgent(
        llm,
        ToolRegistry(),
        _rule_engine(),
        max_steps=2,
        prompt_resolver=resolver,
    )

    agent.run(PORTFOLIO, PRICES)

    messages = llm.calls[0]["messages"]
    assert messages[0]["content"] == "custom rule system"
    assert messages[2]["content"] == "custom rule reminder"
    assert [call[:2] for call in resolver.calls] == [
        ("core.rule-aware.default", "system"),
        ("core.rule-aware.default", "reminder"),
    ]


def test_auditor_default_messages_match_pre_migration_hash():
    llm = FakeLLM([FakeLLMResponse(_audit_response())])
    auditor = LLMAuditor(llm)

    result = auditor.audit_agent_reasoning(
        RULE_DOCUMENTS,
        {"portfolio": PORTFOLIO, "prices": PRICES},
        "Checked R0-01 and held.",
    )

    packed = json.dumps(
        llm.calls[0]["messages"],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert sha256(packed.encode("utf-8")).hexdigest() == AUDIT_MESSAGES_GOLDEN_HASH
    assert result["final_normalized_score"] == 0.75


def test_auditor_uses_injected_profile_slots():
    resolver = _OverrideResolver(
        {
            ("core.compliance-audit.default", "system"): "custom audit system",
            ("core.compliance-audit.default", "user"): "custom audit user",
        }
    )
    llm = FakeLLM([FakeLLMResponse(_audit_response())])
    auditor = LLMAuditor(llm, prompt_resolver=resolver)

    auditor.audit_agent_reasoning(
        RULE_DOCUMENTS,
        {"portfolio": PORTFOLIO, "prices": PRICES},
        "Checked R0-01 and held.",
    )

    assert llm.calls[0]["messages"][0] == {
        "role": "system",
        "content": "custom audit system",
    }
    assert llm.calls[0]["messages"][1] == {
        "role": "user",
        "content": "custom audit user",
    }
    assert [call[:2] for call in resolver.calls] == [
        ("core.compliance-audit.default", "system"),
        ("core.compliance-audit.default", "user"),
    ]
    user_variables = resolver.calls[1][2]
    assert user_variables["rules"] == RULE_DOCUMENTS
    assert "Cash: $10,000.00" in user_variables["market_state"]
    assert user_variables["agent_output"] == "Checked R0-01 and held."
