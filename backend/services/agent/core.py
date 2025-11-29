# services/agent/core.py
import json
import logging
from typing import Dict, Any, List
from .llm_client import LLMClient
from .tools import ToolRegistry
from config.agent_config import AgentConfig

# Define loggers
logger = logging.getLogger(__name__)
llm_logger = logging.getLogger("llm_trace")
agent_logger = logging.getLogger("agent_decision")

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
        # Log start of decision process
        logger.info("Starting agent decision process")
        agent_logger.info("=== Starting New Decision Process ===")
        agent_logger.info(f"Portfolio: {json.dumps(portfolio, ensure_ascii=False)}")
        agent_logger.info(f"Prices: {json.dumps(prices, ensure_ascii=False)}")

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

        for step in range(self.max_steps):
            # Requirement 1: Log raw LLM request
            llm_logger.info(f"--- Step {step+1}/{self.max_steps} Request ---")
            llm_logger.info(json.dumps(messages, ensure_ascii=False, indent=2))
            
            logger.info(f"Initiating LLM request (Step {step+1})")

            resp = self.llm.call(messages, tools=self.tools.openai_tools)
            
            # Requirement 1: Log raw LLM response
            llm_logger.info(f"--- Step {step+1}/{self.max_steps} Response ---")
            llm_logger.info(json.dumps(resp, ensure_ascii=False, indent=2))

            tool_calls = resp["tool_calls"]
            content = resp["content"]

            # Requirement 2: Log LLM output content and tool calls
            agent_logger.info(f"--- Step {step+1} LLM Output ---")
            agent_logger.info(f"Content: {content}")
            if tool_calls:
                agent_logger.info(f"Tool Calls: {json.dumps([t.model_dump() if hasattr(t, 'model_dump') else str(t) for t in tool_calls], ensure_ascii=False)}")

            # 1) 有工具调用：执行工具并把结果回传给模型
            if tool_calls:
                logger.info(f"LLM requested {len(tool_calls)} tool calls")
                for tc in tool_calls:
                    name = tc.function.name
                    args_str = tc.function.arguments or "{}"
                    args = json.loads(args_str)
                    
                    # Console output (Simple)
                    logger.info(f"Executing tool: {name}")
                    
                    # File output (Detailed)
                    agent_logger.info(f"Executing tool '{name}' with args: {args_str}")

                    tool = self.tools.get(name)
                    result = tool(**args)
                    
                    # Log tool result
                    agent_logger.info(f"Tool '{name}' result: {json.dumps(result, ensure_ascii=False)}")

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
            if not content:
                error_msg = "LLM returned empty content"
                logger.error(error_msg)
                agent_logger.error(error_msg)
                raise ValueError(error_msg)

            text = content.strip()
            # 兼容 ```json ... ``` 包裹
            if "```json" in text:
                text = text.split("```json", 1)[1].split("```", 1)[0].strip()
            elif text.startswith("```"):
                text = text.strip("`").strip()

            try:
                decision = json.loads(text)
                
                # Log final decision
                logger.info(f"Agent reached final decision: {decision.get('operation')} {decision.get('symbol', '')}")
                agent_logger.info(f"Final Decision Parsed: {json.dumps(decision, ensure_ascii=False)}")

                # 简单做一下字段兜底，保持与旧逻辑兼容
                if "leverage" not in decision or not decision["leverage"]:
                    decision["leverage"] = 1
                if "direction" not in decision or not decision["direction"]:
                    decision["direction"] = "long"
                else:
                    decision["direction"] = decision["direction"].lower()

                return decision
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse decision JSON: {e}")
                agent_logger.error(f"JSON Parse Error: {e}. Content: {text}")
                raise

        # 超过 max_steps 还没给出最终决策，保守 hold
        logger.warning("Agent exceeded max steps, fallback to HOLD")
        agent_logger.warning("Exceeded max steps, returning fallback HOLD decision")
        return {
            "operation": "hold",
            "symbol": "",
            "direction": "long",
            "target_portion_of_balance": 0.0,
            "leverage": 1,
            "reason": "max_steps reached, fallback hold",
        }
