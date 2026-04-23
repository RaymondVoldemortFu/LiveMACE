"""LLMClient 对 tool_calls 扩展字段（如 thought_signature）的原样回传。"""
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from openai._utils import maybe_transform  
from openai.types.chat import completion_create_params  
from openai.types.chat.chat_completion_message import ChatCompletionMessage  
from openai.types.chat.chat_completion_message_function_tool_call import (  
    ChatCompletionMessageFunctionToolCall,
)

from services.agent.llm_client import LLMClient  


def _body_after_transform(messages: list) -> list:
    """与 OpenAI SDK chat.completions.create 内部一致，验证发往网关的 messages 形态。"""
    body = maybe_transform(
        {"messages": messages, "model": "gemini-proxy-test"},
        completion_create_params.CompletionCreateParamsNonStreaming,
    )
    return body["messages"]


def test_tool_call_roundtrip_preserves_thought_signature_on_call_and_function():
    raw = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "get_account_state",
            "arguments": "{}",
            "thought_signature": "inner_opaque_token_xyz",
        },
        "thought_signature": "outer_opaque_token_abc",
    }
    tc = ChatCompletionMessageFunctionToolCall.model_validate(raw)
    out = LLMClient._tool_call_dict_roundtrip(tc)
    assert out["id"] == "call_1"
    assert out["type"] == "function"
    assert out["thought_signature"] == "outer_opaque_token_abc"
    assert out["function"]["name"] == "get_account_state"
    assert out["function"]["arguments"] == "{}"
    assert out["function"]["thought_signature"] == "inner_opaque_token_xyz"


def test_build_message_dict_does_not_strip_tool_extras():
    tc = ChatCompletionMessageFunctionToolCall.model_validate(
        {
            "id": "c2",
            "type": "function",
            "function": {
                "name": "foo",
                "arguments": '{"a":1}',
                "thought_signature": "fn_level",
            },
        }
    )
    msg = ChatCompletionMessage(role="assistant", content=None, tool_calls=[tc])
    d = LLMClient.build_message_dict(msg)
    assert len(d["tool_calls"]) == 1
    assert d["tool_calls"][0]["function"]["thought_signature"] == "fn_level"


def _assert_signatures_in_history(messages: list, *, round1_ids: tuple[str, str]) -> None:
    """第一轮 assistant 在 messages 中的索引固定为 2（system, user, assistant）。"""
    a1, a2 = round1_ids
    ast1 = messages[2]
    assert ast1["role"] == "assistant"
    tcs = ast1["tool_calls"]
    assert len(tcs) == 2
    # 并行：A 带内外签名；B 自带 inner；B 缺失的 outer 由 _fill_parallel_thought_signatures 从 A 补全
    by_id = {t["id"]: t for t in tcs}
    assert by_id[a1]["thought_signature"] == "sig_turn1_call_A_outer"
    assert by_id[a1]["function"]["thought_signature"] == "sig_turn1_call_A_inner"
    assert by_id[a2]["thought_signature"] == "sig_turn1_call_A_outer"
    assert by_id[a2]["function"]["thought_signature"] == "sig_turn1_call_B_inner"


def test_multi_turn_history_preserves_thought_signatures_through_json_roundtrip():
    """
    模拟 ReAct：两轮 assistant（均有 tool_calls），中间穿插 tool 消息。
    经 json.dumps/loads（贴近请求体序列化）后，各轮 tool_call 上的签名必须仍在。
    """
    tc_a = ChatCompletionMessageFunctionToolCall.model_validate(
        {
            "id": "call_turn1_a",
            "type": "function",
            "function": {
                "name": "get_market_snapshot",
                "arguments": "{}",
                "thought_signature": "sig_turn1_call_A_inner",
            },
            "thought_signature": "sig_turn1_call_A_outer",
        }
    )
    tc_b = ChatCompletionMessageFunctionToolCall.model_validate(
        {
            "id": "call_turn1_b",
            "type": "function",
            "function": {
                "name": "get_account_state",
                "arguments": "{}",
                "thought_signature": "sig_turn1_call_B_inner",
            },
        }
    )
    assistant_round1 = ChatCompletionMessage(
        role="assistant",
        content="First reasoning step.",
        tool_calls=[tc_a, tc_b],
    )

    messages: list = [
        {"role": "system", "content": "You are a trading agent."},
        {"role": "user", "content": '{"portfolio":{}, "prices":{}}'},
        LLMClient.build_message_dict(assistant_round1),
        {
            "role": "tool",
            "tool_call_id": "call_turn1_a",
            "name": "get_market_snapshot",
            "content": '{"ok":true}',
        },
        {
            "role": "tool",
            "tool_call_id": "call_turn1_b",
            "name": "get_account_state",
            "content": '{"balance":1}',
        },
    ]

    # 模拟发往网关前的序列化 / 日志 / 缓存等
    wire1 = json.dumps(messages, ensure_ascii=False)
    messages = json.loads(wire1)
    _assert_signatures_in_history(messages, round1_ids=("call_turn1_a", "call_turn1_b"))

    tc_c = ChatCompletionMessageFunctionToolCall.model_validate(
        {
            "id": "call_turn2_only",
            "type": "function",
            "function": {
                "name": "execute_trade",
                "arguments": '{"symbol":"SPY","side":"buy"}',
                "thought_signature": "sig_turn2_fn_inner",
            },
            "thought_signature": "sig_turn2_outer",
        }
    )
    assistant_round2 = ChatCompletionMessage(
        role="assistant",
        content="Second step after tools.",
        tool_calls=[tc_c],
    )
    messages.append(LLMClient.build_message_dict(assistant_round2))

    wire2 = json.dumps(messages, ensure_ascii=False)
    messages_final = json.loads(wire2)

    _assert_signatures_in_history(messages_final, round1_ids=("call_turn1_a", "call_turn1_b"))

    ast2 = messages_final[-1]
    assert ast2["role"] == "assistant"
    assert len(ast2["tool_calls"]) == 1
    tc = ast2["tool_calls"][0]
    assert tc["id"] == "call_turn2_only"
    assert tc["thought_signature"] == "sig_turn2_outer"
    assert tc["function"]["thought_signature"] == "sig_turn2_fn_inner"
    assert tc["function"]["name"] == "execute_trade"


def test_dict_tool_call_roundtrip_does_not_wipe_id_and_function():
    """回归：dict 不能用 getattr(tc,'function')，否则会得到 id=''、function={}。"""
    tc = {
        "id": "call_turn1_b",
        "type": "function",
        "function": {
            "name": "get_account_state",
            "arguments": "{}",
            "thought_signature": "sig_inner_B",
        },
    }
    out = LLMClient._tool_call_dict_roundtrip(tc)
    assert out["id"] == "call_turn1_b"
    assert out["function"]["name"] == "get_account_state"
    assert out["function"]["thought_signature"] == "sig_inner_B"


def test_three_rounds_history_signatures_survive_normalize_and_openai_transform():
    """
    至少三轮「assistant + tool」交互；messages 中 tool_calls 均为 dict（与 ReAct 一致）。
    每轮后：json 往返 + LLMClient 规范化 + OpenAI maybe_transform，签名必须仍在。
    """
    messages: list = [
        {"role": "system", "content": "agent"},
        {"role": "user", "content": "{}"},
    ]

    def assistant_with_tool(
        call_id: str,
        name: str,
        *,
        outer_sig: str | None,
        inner_sig: str,
        content: str,
    ) -> dict:
        fn: dict = {"name": name, "arguments": "{}", "thought_signature": inner_sig}
        tc: dict = {"id": call_id, "type": "function", "function": fn}
        if outer_sig is not None:
            tc["thought_signature"] = outer_sig
        return {"role": "assistant", "content": content, "tool_calls": [tc]}

    # Round 1
    messages.append(
        assistant_with_tool(
            "r1",
            "get_market_snapshot",
            outer_sig="sig_r1_outer",
            inner_sig="sig_r1_inner",
            content="step1",
        )
    )
    messages.append({"role": "tool", "tool_call_id": "r1", "name": "get_market_snapshot", "content": "{}"})

    wire = json.dumps(messages, ensure_ascii=False)
    messages = json.loads(wire)
    messages = LLMClient._normalize_messages_for_api(messages)
    t1 = _body_after_transform(messages)
    assert t1[2]["tool_calls"][0]["thought_signature"] == "sig_r1_outer"
    assert t1[2]["tool_calls"][0]["function"]["thought_signature"] == "sig_r1_inner"

    # Round 2（仅 function 内签名，模拟并行中第二个调用）
    messages.append(
        assistant_with_tool(
            "r2",
            "get_account_state",
            outer_sig=None,
            inner_sig="sig_r2_inner_only",
            content="step2",
        )
    )
    messages.append({"role": "tool", "tool_call_id": "r2", "name": "get_account_state", "content": "{}"})

    wire = json.dumps(messages, ensure_ascii=False)
    messages = json.loads(wire)
    messages = LLMClient._normalize_messages_for_api(messages)
    t2 = _body_after_transform(messages)
    assert t2[2]["tool_calls"][0]["function"]["thought_signature"] == "sig_r1_inner"
    assert t2[4]["tool_calls"][0]["function"]["thought_signature"] == "sig_r2_inner_only"
    assert "thought_signature" not in t2[4]["tool_calls"][0]

    # Round 3
    messages.append(
        assistant_with_tool(
            "r3",
            "execute_trade",
            outer_sig="sig_r3_outer",
            inner_sig="sig_r3_inner",
            content="step3",
        )
    )
    messages.append({"role": "tool", "tool_call_id": "r3", "name": "execute_trade", "content": "{}"})

    wire = json.dumps(messages, ensure_ascii=False)
    messages = json.loads(wire)
    messages = LLMClient._normalize_messages_for_api(messages)
    t3 = _body_after_transform(messages)
    # 三轮 assistant 在索引 2, 4, 6
    assert t3[2]["tool_calls"][0]["function"]["thought_signature"] == "sig_r1_inner"
    assert t3[4]["tool_calls"][0]["function"]["thought_signature"] == "sig_r2_inner_only"
    assert t3[6]["tool_calls"][0]["thought_signature"] == "sig_r3_outer"
    assert t3[6]["tool_calls"][0]["function"]["thought_signature"] == "sig_r3_inner"


def test_three_rounds_camel_case_thought_signature_preserved():
    """网关若返回 camelCase，也应原样保留（不经 snake 改写）。"""
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    for i in range(3):
        cid = f"c{i}"
        messages.append(
            {
                "role": "assistant",
                "content": f"r{i}",
                "tool_calls": [
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": f"f{i}",
                            "arguments": "{}",
                            "thoughtSignature": f"inner_{i}",
                        },
                        "thoughtSignature": f"outer_{i}",
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": cid, "name": f"f{i}", "content": "{}"})

    messages = LLMClient._normalize_messages_for_api(messages)
    body = _body_after_transform(messages)
    for i in range(3):
        ast = body[2 + i * 2]
        tc = ast["tool_calls"][0]
        assert tc["thoughtSignature"] == f"outer_{i}"
        assert tc["function"]["thoughtSignature"] == f"inner_{i}"


def test_parallel_three_tool_calls_only_first_has_signatures_like_gemini():
    """
    >2 次并行 tool-call，仅第一条响应带 thought_signature（常见 Gemini）。
    网关要求每个 functionCall part 均带签名；应补全且不丢 id/name。
    """
    tcs_raw = [
        ChatCompletionMessageFunctionToolCall.model_validate(
            {
                "id": "c0",
                "type": "function",
                "function": {
                    "name": "get_market_snapshot",
                    "arguments": "{}",
                    "thought_signature": "inner_tpl",
                },
                "thought_signature": "outer_tpl",
            }
        ),
        ChatCompletionMessageFunctionToolCall.model_validate(
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "get_account_state", "arguments": "{}"},
            }
        ),
        ChatCompletionMessageFunctionToolCall.model_validate(
            {
                "id": "c2",
                "type": "function",
                "function": {"name": "get_kline_history", "arguments": "{}"},
            }
        ),
    ]
    msg = ChatCompletionMessage(role="assistant", content="parallel batch", tool_calls=tcs_raw)
    bd = LLMClient.build_message_dict(msg)
    assert len(bd["tool_calls"]) == 3
    for tc in bd["tool_calls"]:
        assert tc["thought_signature"] == "outer_tpl"
        assert tc["function"]["thought_signature"] == "inner_tpl"
    names = [t["function"]["name"] for t in bd["tool_calls"]]
    assert names == ["get_market_snapshot", "get_account_state", "get_kline_history"]

    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, bd]
    norm = LLMClient._normalize_messages_for_api(hist)
    body = _body_after_transform(norm)
    ast = body[2]
    assert len(ast["tool_calls"]) == 3
    for t in ast["tool_calls"]:
        assert t["function"]["thought_signature"] == "inner_tpl"
        assert t["thought_signature"] == "outer_tpl"


def test_parallel_fill_does_not_overwrite_existing_inner_signature():
    """已有 function.thought_signature 的并行调用保持原值，只补缺失的顶层。"""
    tc1 = ChatCompletionMessageFunctionToolCall.model_validate(
        {
            "id": "a",
            "type": "function",
            "function": {"name": "x", "arguments": "{}", "thought_signature": "inner_a"},
            "thought_signature": "outer_tpl",
        }
    )
    tc2 = ChatCompletionMessageFunctionToolCall.model_validate(
        {
            "id": "b",
            "type": "function",
            "function": {"name": "get_account_state", "arguments": "{}", "thought_signature": "inner_b_only"},
        }
    )
    bd = LLMClient.build_message_dict(
        ChatCompletionMessage(role="assistant", content="m", tool_calls=[tc1, tc2])
    )
    by = {t["id"]: t for t in bd["tool_calls"]}
    assert by["a"]["function"]["thought_signature"] == "inner_a"
    assert by["b"]["function"]["thought_signature"] == "inner_b_only"
    assert by["b"]["thought_signature"] == "outer_tpl"


def test_parallel_outer_only_on_first_mirrors_to_function_on_all_parts():
    """
    网关常见形态：只在 tool_call 顶层有 thought_signature，function 对象内完全无签名。
    必须把 outer 复制为各 part 的 function.thought_signature，否则 position 2 仍 400。
    """
    msg = ChatCompletionMessage(
        role="assistant",
        content="batch",
        tool_calls=[
            ChatCompletionMessageFunctionToolCall.model_validate(
                {
                    "id": "p0",
                    "type": "function",
                    "function": {"name": "get_market_snapshot", "arguments": "{}"},
                    "thought_signature": "only_on_tool_call_level",
                }
            ),
            ChatCompletionMessageFunctionToolCall.model_validate(
                {
                    "id": "p1",
                    "type": "function",
                    "function": {"name": "get_account_state", "arguments": "{}"},
                }
            ),
        ],
    )
    bd = LLMClient.build_message_dict(msg, model="gemini-2.0-flash")
    for tc in bd["tool_calls"]:
        assert tc["thought_signature"] == "only_on_tool_call_level"
        assert tc["function"]["thought_signature"] == "only_on_tool_call_level"

    hist = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}, bd]
    norm = LLMClient._normalize_messages_for_api(hist, model="gemini-2.0-flash")
    body = _body_after_transform(norm)
    for t in body[2]["tool_calls"]:
        assert t["function"]["thought_signature"] == "only_on_tool_call_level"


def test_parallel_no_signatures_gemini_non_empty_placeholder():
    """并行且全无签名时，Gemini 下写入非空占位（空串会被部分网关视为缺失）。"""
    msg = ChatCompletionMessage(
        role="assistant",
        content="x",
        tool_calls=[
            ChatCompletionMessageFunctionToolCall.model_validate(
                {
                    "id": "a",
                    "type": "function",
                    "function": {"name": "get_market_snapshot", "arguments": "{}"},
                }
            ),
            ChatCompletionMessageFunctionToolCall.model_validate(
                {
                    "id": "b",
                    "type": "function",
                    "function": {"name": "get_account_state", "arguments": "{}"},
                }
            ),
        ],
    )
    bd = LLMClient.build_message_dict(msg, model="gemini-2.0-flash")
    for tc in bd["tool_calls"]:
        assert tc.get("thought_signature")
        assert tc["function"].get("thought_signature")
        assert tc.get("thoughtSignature") == tc.get("thought_signature")
        assert tc["function"].get("thoughtSignature") == tc["function"].get("thought_signature")


def test_parallel_no_signatures_non_gemini_not_mutated():
    """非 Gemini 模型：无法推断签名时不强行占位（避免污染 OpenAI 官方等）。"""
    raw = [
        {
            "id": "a",
            "type": "function",
            "function": {"name": "x", "arguments": "{}"},
        },
        {
            "id": "b",
            "type": "function",
            "function": {"name": "y", "arguments": "{}"},
        },
    ]
    filled = LLMClient._fill_parallel_thought_signatures(raw, model="gpt-4.1")
    assert filled == raw


def test_multi_turn_dict_only_history_still_valid_for_next_request_shape():
    """
    第二轮请求时 history 应全是 dict（与 OpenAI messages 参数一致）；
    确认首轮 assistant 的 tool_calls 仍为网关可接受的嵌套结构。
    """
    tc = ChatCompletionMessageFunctionToolCall.model_validate(
        {
            "id": "id1",
            "type": "function",
            "function": {
                "name": "x",
                "arguments": "{}",
                "thought_signature": "preserve_me",
            },
            "thought_signature": "outer1",
        }
    )
    hist = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        LLMClient.build_message_dict(
            ChatCompletionMessage(role="assistant", content="c", tool_calls=[tc])
        ),
        {"role": "tool", "tool_call_id": "id1", "name": "x", "content": "{}"},
    ]
    payload = {"model": "gemini-test", "messages": hist}
    restored = json.loads(json.dumps(payload))["messages"]
    assert restored[2]["tool_calls"][0]["thought_signature"] == "outer1"
    assert restored[2]["tool_calls"][0]["function"]["thought_signature"] == "preserve_me"
