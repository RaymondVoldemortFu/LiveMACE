# services/agent/core.py
import json
from .llm_client import LLMClient
from .tools import ToolRegistry

SYSTEM_PROMPT = """
你是一个加密货币交易 Agent。
你可以使用工具获取行情、账户、并执行模拟下单。

任务流程：
1. 如果需要市场或账户数据，请调用工具，而不是自己假设数据。
2. 最终必须输出一个严格 JSON 的决策对象：
{
  "operation": "open" | "close" | "hold",
  "symbol": "...",
  "direction": "long" | "short",
  "size": 0.1,
  "leverage": 3,
  "reason": "..."
}
"""

class TradingAgent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, max_steps=4):
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps

    def run(self, symbol: str):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"请分析 {symbol} 的交易机会。"}
        ]

        for step in range(self.max_steps):
            resp = self.llm.call(messages, tools=self.tools.openai_tools)

            # 工具调用
            if resp["tool_calls"]:
                for tc in resp["tool_calls"]:
                    name = tc.function.name
                    args = json.loads(tc.function.arguments)

                    tool = self.tools.get(name)
                    result = tool(**args)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

                continue  # 下一轮，多次工具调用 OK

            # 最终输出 JSON
            final_text = resp["content"]
            return json.loads(final_text)

        # Fallback
        return {
            "operation": "hold",
            "symbol": symbol,
            "reason": "max_steps fallback"
        }
