import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.agent.llm_client import LLMClient


def _tc(idx: int, *, tool: str = "get_market_snapshot", args: str = "{}") -> dict:
    return {
        "id": f"call_{idx}",
        "type": "function",
        "function": {
            "name": tool,
            "arguments": args,
        },
    }


def test_apply_tool_call_guardrails_caps_to_20_and_warns():
    tool_calls = [_tc(i, tool=f"tool_{i}", args=f'{{"x": {i}}}') for i in range(25)]
    guarded, warnings = LLMClient.apply_tool_call_guardrails(tool_calls, model="gpt-4.1")

    assert len(guarded) == LLMClient.MAX_TOOL_CALLS_PER_ASSISTANT_TURN == 20
    assert guarded[0]["id"] == "call_0"
    assert guarded[-1]["id"] == "call_19"
    assert any("Tool-call limit exceeded" in w for w in warnings)


def test_apply_tool_call_guardrails_filters_repeated_identical_calls_and_warns():
    tool_calls = [
        _tc(1, tool="get_kline_history", args='{"symbol":"BTC","period":"5m"}'),
        _tc(2, tool="get_kline_history", args='{"period":"5m","symbol":"BTC"}'),
        _tc(3, tool="get_kline_history", args='{"symbol":"ETH","period":"5m"}'),
    ]
    guarded, warnings = LLMClient.apply_tool_call_guardrails(tool_calls, model="gpt-4.1")

    assert len(guarded) == 2
    assert guarded[0]["id"] == "call_1"
    assert guarded[1]["id"] == "call_3"
    assert any("Repeated identical tool calls detected" in w for w in warnings)


def test_apply_tool_call_guardrails_keeps_same_tool_name_with_different_arguments():
    tool_calls = [
        _tc(1, tool="consult_search_agent", args='{"query":"btc news"}'),
        _tc(2, tool="consult_search_agent", args='{"query":"eth news"}'),
    ]
    guarded, warnings = LLMClient.apply_tool_call_guardrails(tool_calls, model="gpt-4.1")

    assert len(guarded) == 2
    assert warnings == []


def test_guardrail_warning_message_is_model_readable():
    msg = LLMClient.tool_guardrail_warning_user_message(
        [
            "Tool-call limit exceeded: requested 1000, capped at 20, dropped 980.",
            "Repeated identical tool calls detected in one response; filtered 5 duplicate calls.",
        ]
    )
    assert msg["role"] == "user"
    assert "Guardrail warning" in msg["content"]
    assert "capped at 20" in msg["content"]
    assert "filtered 5 duplicate calls" in msg["content"]


def test_all_agent_tool_execution_paths_apply_guardrails():
    files = [
        BACKEND_DIR / "services/agent/react.py",
        BACKEND_DIR / "services/agent/rule_aware/rule_aware_agent.py",
        BACKEND_DIR / "services/agent/multi_agent.py",
        BACKEND_DIR / "services/agent/multi_agent_advanced.py",
        BACKEND_DIR / "services/agent/sub_agents/search_agent.py",
    ]
    for file_path in files:
        text = file_path.read_text(encoding="utf-8")
        assert "apply_tool_call_guardrails(" in text, f"missing guardrail usage in {file_path}"
