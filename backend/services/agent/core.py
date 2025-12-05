# services/agent/core.py
import re
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Callable, Optional
from .llm_client import LLMClient
from .tools import ToolRegistry
from config.agent_config import AgentConfig
from services.agent.prompts.system_prompts import TRADE_AGENT_PROMPT

# Define loggers
logger = logging.getLogger(__name__)
llm_logger = logging.getLogger("llm_trace")
agent_logger = logging.getLogger("agent_decision")

SYSTEM_PROMPT = TRADE_AGENT_PROMPT

class TradingAgent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, max_steps: int = AgentConfig.MAX_STEPS):
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps

    def run(self, portfolio: Dict[str, Any], prices: Dict[str, float], on_step: Optional[Callable[[Dict], None]] = None) -> Dict[str, Any]:
        """
        输入:
            portfolio: 和原 call_ai_for_decision 中一致的结构
            prices: symbol -> price 的字典
            on_step: Optional callback function called after each step with the message dict
        输出:
            与原先 call_ai_for_decision 返回值同结构的决策 dict
        """
        # Log start of decision process
        logger.info("Starting agent decision process")
        agent_logger.info("=== Starting New Decision Process ===")
        agent_logger.info(f"Portfolio: {json.dumps(portfolio, ensure_ascii=False)}")
        agent_logger.info(f"Prices: {json.dumps(prices, ensure_ascii=False)}")

        # Get current UTC+8 time
        tz_utc_8 = timezone(timedelta(hours=8))
        current_time = datetime.now(tz_utc_8).strftime("%Y-%m-%d %H:%M:%S")
        
        # Add time context to system prompt
        system_prompt_with_time = f"{SYSTEM_PROMPT}\n\nCurrent Time (UTC+8): {current_time}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt_with_time},
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
            # Check if we need to remind the agent about remaining steps
            remaining_steps = self.max_steps - step
            request_messages = list(messages)
            
            if remaining_steps < AgentConfig.STEP_REMINDER_THRESHOLD:
                logger.info(f"Adding step reminder (Remaining: {remaining_steps})")
                request_messages.append({
                    "role": "user", 
                    "content": f"Reminder: You have {remaining_steps} steps remaining. You must output <FINAL_JSON> before running out of steps."
                })

            # Requirement 1: Log raw LLM request
            llm_logger.info(f"--- Step {step+1}/{self.max_steps} Request ---")
            llm_logger.info(json.dumps(request_messages, ensure_ascii=False, indent=2))
            
            logger.info(f"Initiating LLM request (Step {step+1})")

            resp = self.llm.call(request_messages, tools=self.tools.openai_tools)
            
            # Convert to dict for consistent handling and logging
            if hasattr(resp, "model_dump"):
                resp_dict = resp.model_dump()
            else:
                resp_dict = resp.dict()

            # Requirement 1: Log raw LLM response
            llm_logger.info(f"--- Step {step+1}/{self.max_steps} Response ---")
            llm_logger.info(json.dumps(resp_dict, ensure_ascii=False, indent=2))

            # Important: Add assistant response to history
            messages.append(resp_dict)
            if on_step:
                on_step(resp_dict)

            tool_calls = resp.tool_calls
            content = resp.content

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

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                    messages.append(tool_msg)
                    if on_step:
                        on_step(tool_msg)
                continue

            # 2) 没有工具调用，视为最终决策，尝试解析 JSON
            # 优先检查是否有 <FINAL_JSON> 标签
            final_decision = None
            if content:
                match = re.search(r"<FINAL_JSON>(.*?)</FINAL_JSON>", content, re.DOTALL)
                if match:
                    json_str = match.group(1).strip()
                    try:
                        decision = json.loads(json_str)
                        
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
                        # 如果解析失败，使用默认 (Fallback HOLD)
                        logger.error(f"Failed to parse decision JSON within <FINAL_JSON>: {e}. Returning fallback HOLD.")
                        agent_logger.error(f"JSON Parse Error in <FINAL_JSON>: {e}. Content: {json_str}")
                        # Return fallback directly
                        return {
                            "operation": "hold",
                            "symbol": "",
                            "direction": "long",
                            "target_portion_of_balance": 0.0,
                            "leverage": 1,
                            "reason": "JSON Parse Error in <FINAL_JSON>, fallback hold",
                        }

            # 如果没有工具调用，且没有 <FINAL_JSON>，且没有内容 -> 异常
            if not tool_calls and not content:
                error_msg = "LLM returned empty content and no tool calls"
                logger.error(error_msg)
                agent_logger.error(error_msg)
                raise ValueError(error_msg)

            # 如果没有工具调用，且没有 <FINAL_JSON>，但有内容 -> 视为中间思考过程，继续循环
            if not tool_calls:
                logger.info("No tool calls and no <FINAL_JSON> found. Continuing conversation (thought step).")
                continue

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
