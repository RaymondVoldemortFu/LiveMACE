"""
Gemini 经 OpenAI 兼容网关的连通性与工具调用集成测试。

从 backend/.env 读取凭据（运行前请配置其一）：
- 推荐：GEMINI_OPENAI_COMPAT_MODEL / GEMINI_OPENAI_COMPAT_API_KEY / GEMINI_OPENAI_COMPAT_BASE_URL
- 或：EVAL_LLM_MODEL（名称含 gemini）+ EVAL_LLM_API_KEY + EVAL_LLM_BASE_URL
- 或：EVAL_LLM_MODEL（含 gemini）+ API_KEY + BASE_URL

未配置完整时相关用例 skip，不视为失败。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(BACKEND_DIR / ".env")

from services.agent.llm_client import LLMClient
from services.agent.react import ReActAgent
from services.agent.tools import Tool, ToolRegistry


def _gemini_openai_compat_client() -> LLMClient | None:
    model = (
        os.getenv("GEMINI_OPENAI_COMPAT_MODEL")
        or os.getenv("EVAL_LLM_MODEL")
        or ""
    ).strip()
    if not LLMClient.is_gemini_model_name(model):
        return None
    key = (
        os.getenv("GEMINI_OPENAI_COMPAT_API_KEY")
        or os.getenv("EVAL_LLM_API_KEY")
        or os.getenv("API_KEY")
        or ""
    ).strip()
    base = (
        os.getenv("GEMINI_OPENAI_COMPAT_BASE_URL")
        or os.getenv("EVAL_LLM_BASE_URL")
        or os.getenv("BASE_URL")
        or ""
    ).strip()
    if not key or not base:
        return None
    return LLMClient(model=model, api_key=key, base_url=base)


@pytest.fixture(scope="module")
def gemini_llm() -> LLMClient:
    client = _gemini_openai_compat_client()
    if client is None:
        pytest.skip(
            "缺少 .env 中的 Gemini OpenAI 兼容配置；见本文件模块 docstring。"
        )
    return client


def test_gemini_model_uses_openai_sdk_not_separate_branch():
    llm = LLMClient(model="gemini-2.0-flash", api_key="sk-placeholder", base_url="https://example.com/v1")
    assert hasattr(llm.client, "chat")
    assert hasattr(llm.client.chat, "completions")


@pytest.mark.integration
def test_gemini_connection_openai_compat(gemini_llm: LLMClient):
    text = gemini_llm.test_connection(timeout_seconds=60.0)
    assert "connection test successful" in text.lower()


@pytest.mark.integration
def test_gemini_tool_call_openai_compat(gemini_llm: LLMClient):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "record_temperature_celsius",
                "description": "Record the target room temperature in degrees Celsius.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "celsius": {
                            "type": "number",
                            "description": "Temperature in Celsius.",
                        }
                    },
                    "required": ["celsius"],
                },
            },
        }
    ]
    messages = [
        {
            "role": "system",
            "content": "You have a tool to record temperature. When the user asks to set a temperature, call the tool once with the numeric value.",
        },
        {
            "role": "user",
            "content": "Please record that the target temperature is 21.5 degrees Celsius using the tool.",
        },
    ]

    kwargs = {
        "model": gemini_llm.model,
        "messages": messages,
        "tools": tools,
        "temperature": 0.2,
        "max_tokens": 512,
        "timeout": 90.0,
        "tool_choice": "required",
    }

    try:
        response = gemini_llm.client.chat.completions.create(**kwargs)
    except Exception as e:
        if "tool_choice" in str(e).lower() or "400" in str(e):
            kwargs.pop("tool_choice", None)
            response = gemini_llm.client.chat.completions.create(**kwargs)
        else:
            raise

    msg = response.choices[0].message
    assert msg.tool_calls, "期望至少一次 function call；若网关不支持 tool_choice=required，请查看响应文本"
    tc = msg.tool_calls[0]
    assert tc.function.name == "record_temperature_celsius"
    args = json.loads(tc.function.arguments or "{}")
    assert isinstance(args.get("celsius"), (int, float))
    assert abs(float(args["celsius"]) - 21.5) < 1.0


def _tools_market_and_account() -> list[dict]:
    """与交易 agent 常见首步一致：行情 + 账户，便于触发并行 tool_calls。"""
    empty_obj = {"type": "object", "properties": {}}

    def fn(name: str, desc: str) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": empty_obj,
            },
        }

    return [
        fn("get_market_snapshot", "Return a market snapshot for configured symbols."),
        fn("get_account_state", "Return current account balances and positions."),
    ]


@pytest.mark.integration
def test_gemini_two_turn_tool_then_followup(gemini_llm: LLMClient):
    """
    真实两轮对话：首轮模型发起 tool call（parallel_tool_calls=False 下多为单条），
    回传 tool 结果后第二轮必须成功（覆盖生产 ReAct 主路径）。
    说明：部分网关会校验 thought_signature，不接受客户端伪造的并行历史，故不用合成并行 assistant。
    """
    tools = _tools_market_and_account()
    messages = [
        {
            "role": "system",
            "content": (
                "You are a trading assistant. When asked to refresh data, call get_market_snapshot once "
                "with {} arguments, then wait for the tool result."
            ),
        },
        {"role": "user", "content": "Refresh the market snapshot once using the tool."},
    ]
    first = gemini_llm.call(messages, tools=tools, timeout=120.0)
    assert first.tool_calls, "首轮应产生至少一次 tool call"
    fd = gemini_llm.build_assistant_message_dict(first)
    messages.append(fd)
    tc0 = first.tool_calls[0]
    messages.append(
        {
            "role": "tool",
            "tool_call_id": tc0.id,
            "name": tc0.function.name,
            "content": '{"ok": true}',
        }
    )
    messages.append(LLMClient.gemini_post_tool_user_message())
    second = gemini_llm.call(messages, tools=tools, timeout=120.0)
    text = (gemini_llm.extract_text_content(second) or "").strip()
    assert text, "第二轮应返回非空文本"


def _minimal_trade_registry_for_react() -> ToolRegistry:
    """关闭 tool routing 时 ReAct 会从 DEFAULT_NON_ROUTED 中取交集；注册常用子集即可。"""

    def _empty_tool(name: str, desc: str):
        return Tool(
            name=name,
            description=desc,
            parameters={"type": "object", "properties": {}},
            func=lambda **_: {"stub": True, "tool": name},
        )

    reg = ToolRegistry()
    for name, desc in [
        ("get_market_snapshot", "Market snapshot."),
        ("get_kline_history", "K-line history."),
        ("get_account_state", "Account state."),
        ("get_history_decisions", "Past decisions."),
        ("execute_trade", "Execute a trade (integration stub)."),
    ]:
        reg.register(_empty_tool(name, desc))
    return reg


@pytest.mark.integration
def test_gemini_react_full_decision_flow_stub_tools(gemini_llm: LLMClient):
    """
    真实 ReActAgent.run + 真实 Gemini：覆盖多轮 chat.completions（含并行 tool 后的 history）。
    模型需自行在有限步内结束（工具均为桩，最终多为 hold 或 max_steps）。
    """
    reg = _minimal_trade_registry_for_react()
    agent = ReActAgent(gemini_llm, reg, max_steps=12, user_id="integration-test")
    agent.set_tool_routing_enabled(False)

    decision = agent.run(
        portfolio={"cash_usdt": 1000.0, "positions": []},
        prices={"BTCUSDT": 95000.0},
    )
    assert isinstance(decision, dict)
    assert "operation" in decision
    assert decision["operation"] in ("hold", "buy", "sell")
