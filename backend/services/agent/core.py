# services/agent/core.py
import json
from typing import Dict, Any, List
from .llm_client import LLMClient
from .tools import ToolRegistry
from config.agent_config import AgentConfig

SYSTEM_PROMPT = """
你是一个加密货币交易 Agent。
你可以使用以下工具：
- get_market_snapshot: 查询单个币种的最新价格与市场状态
- get_account_state: 查询账户资金与持仓情况
（工具的具体参数和返回格式会在调用时提供）

你的任务：
- 结合账户持仓、可用资金、行情数据，在多个币种中选择是否进行交易。
- 最终输出一个严格的 JSON 对象，格式如下：

{
  "operation": "open" | "close" | "hold",
  "symbol": "BTC" | "ETH" | "SOL" | "BNB" | "XRP" | "DOGE",
  "direction": "long" | "short",
  "target_portion_of_balance": 0.2,
  "leverage": 3,
  "reason": "简要说明你的决策逻辑"
}

说明：
- operation = "open": 开新仓，direction 决定 long/short，target_portion_of_balance 表示使用可用资金的比例（0~1）。
- operation = "close": 平已有仓位，target_portion_of_balance 表示平仓比例（0~1，1=全平）。
- operation = "hold": 不做任何操作，此时可以省略 direction / target_portion_of_balance / leverage。
- 只能在有价格数据的币种中进行交易。
- 只能对当前持有的仓位进行 close 操作。
- 请严格输出 JSON，不要输出多余文字。
"""

class TradingAgent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, max_steps: int = AgentConfig.MAX_STEPS):
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps

    def run(self, portfolio: Dict[str, Any], prices: Dict[str, float]) -> Dict[str, Any]:
        """
        输入:
            portfolio: 和原 call_ai_for_decision 中一致的结构
            prices: symbol -> price 的字典
        输出:
            与原先 call_ai_for_decision 返回值同结构的决策 dict
        """
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "portfolio": portfolio,
                        "prices": prices,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        for _ in range(self.max_steps):
            resp = self.llm.call(messages, tools=self.tools.openai_tools)
            tool_calls = resp["tool_calls"]

            # 1) 有工具调用：执行工具并把结果回传给模型
            if tool_calls:
                for tc in tool_calls:
                    name = tc.function.name
                    args = json.loads(tc.function.arguments or "{}")
                    tool = self.tools.get(name)
                    result = tool(**args)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": name,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                continue

            # 2) 没有工具调用，视为最终决策，尝试解析 JSON
            content = resp["content"]
            if not content:
                raise ValueError("LLM returned empty content")

            text = content.strip()
            # 兼容 ```json ... ``` 包裹
            if "```json" in text:
                text = text.split("```json", 1)[1].split("```", 1)[0].strip()
            elif text.startswith("```"):
                text = text.strip("`").strip()

            decision = json.loads(text)

            # 简单做一下字段兜底，保持与旧逻辑兼容
            if "leverage" not in decision or not decision["leverage"]:
                decision["leverage"] = 1
            if "direction" not in decision or not decision["direction"]:
                decision["direction"] = "long"
            else:
                decision["direction"] = decision["direction"].lower()

            return decision

        # 超过 max_steps 还没给出最终决策，保守 hold
        return {
            "operation": "hold",
            "symbol": "",
            "direction": "long",
            "target_portion_of_balance": 0.0,
            "leverage": 1,
            "reason": "max_steps reached, fallback hold",
        }
