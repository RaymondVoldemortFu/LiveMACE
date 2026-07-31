import re
import json
import logging
import inspect
from datetime import timezone, timedelta
from typing import Dict, Any, List, Callable, Optional
from .llm_client import LLMClient
from .tools import ToolRegistry
from .tool_selector import (
    ensure_tool_selector_tool,
    REQUIRED_TOOL_NAMES,
    META_TOOL_NAME,
)
from config.agent_config import AgentConfig
from services.agent.prompts.system_prompts import get_trade_agent_prompt
from .base import BaseAgent
from services.time_source import now_in_tz

# Define loggers
logger = logging.getLogger(__name__)
llm_logger = logging.getLogger("llm_trace")
agent_logger = logging.getLogger("agent_decision")
tool_output_logger = logging.getLogger("tool_output")

DEFAULT_NON_ROUTED_TOOL_NAMES = [
    "get_market_snapshot",
    "get_kline_history",
    "get_account_state",
    "get_history_decisions",
    "consult_search_agent",
    "execute_shell_command",
    "read_file",
    "write_file",
    "run_python_script",
    "execute_trade",
    "memory_search",
    "memory_add",
]


# 含 gemini 名称的模型经 OpenAI 兼容网关时，与 GPT 等共用本文件的 ReAct + tool_calls 流程。

class ReActAgent(BaseAgent):
    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        max_steps: int = AgentConfig.MAX_STEPS,
        user_id: str = None,
        agent_name: Optional[str] = None,
    ):
        super().__init__(llm, tools, agent_name=agent_name)
        self.max_steps = max_steps
        self.user_id = user_id
        self.tool_routing_enabled = bool(getattr(AgentConfig, "AGENT_ENABLE_TOOL_ROUTING", True))
        # Memory tools are now registered in env_wrapper.register_default_tools()
        # self.memory = get_memory_service()
        ensure_tool_selector_tool(self.llm, self.tools)

    def set_tool_routing_enabled(self, enabled: bool):
        self.tool_routing_enabled = bool(enabled)

    def _missing_required_args(self, func: Callable, args: Dict[str, Any]) -> List[str]:
        """Return missing required callable parameters."""
        target = getattr(func, "func", func)
        try:
            sig = inspect.signature(target)
        except Exception:
            return []

        missing: List[str] = []
        for name, param in sig.parameters.items():
            if param.kind not in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                continue
            if param.default is inspect._empty and name not in args:
                missing.append(name)
        return missing

    @staticmethod
    def _sanitize_tool_result_for_model(tool_name: str, result: Any) -> Any:
        if tool_name != META_TOOL_NAME or not isinstance(result, dict):
            return result

        return {key: value for key, value in result.items() if not str(key).startswith("_")}

    def _normalize_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        if "leverage" not in decision or not decision["leverage"]:
            decision["leverage"] = 1
        if "direction" not in decision or not decision["direction"]:
            decision["direction"] = "long"
        else:
            decision["direction"] = str(decision["direction"]).lower()
        return decision

    @staticmethod
    def _is_trade_done_message(text: str) -> bool:
        if not text:
            return False
        normalized = text.strip().replace("`", "")
        if normalized == "<TRADE_DONE>":
            return True
        if re.search(r"<\s*TRADE_DONE\s*>", normalized, re.IGNORECASE):
            return True
        squashed = re.sub(r"\s+", "", normalized).upper()
        # Be tolerant to near-miss variants seen in provider outputs.
        if squashed in {"<TRADE_DONE>", "TRADE_DONE>", "<TRADE_DONE", "TRADE_DONE"}:
            return True
        if "TRADE_DONE" in squashed and len(squashed) <= 32:
            return True
        return False

    def run(
        self,
        portfolio: Dict[str, Any],
        prices: Dict[str, float],
        on_step: Optional[Callable[[Dict], None]] = None,
        trace_id: Optional[str] = None,
        decision_round_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        输入:
            portfolio: 和 Agent 决策上下文一致的结构
            prices: symbol -> price 的字典
            on_step: Optional callback function called after each step with the message dict
            trace_id: Trace ID for current session
        输出:
            Agent 决策 dict
        """
        # Log start of decision process
        logger.info("Starting agent decision process (ReAct Architecture)")
        agent_logger.info("=== Starting New Decision Process (ReAct) ===")
        agent_logger.info(f"Portfolio: {json.dumps(portfolio, ensure_ascii=False)}")
        agent_logger.info(f"Prices: {json.dumps(prices, ensure_ascii=False)}")

        # Check if memory tools are available
        has_memory = any(tool.name in ['memory_add', 'memory_search'] for tool in self.tools.tools.values())
        system_prompt = get_trade_agent_prompt(
            memory_enabled=has_memory,
            tool_routing_enabled=self.tool_routing_enabled,
        )

        # Get current UTC+8 time
        tz_utc_8 = timezone(timedelta(hours=8))
        current_time = now_in_tz(tz_utc_8).strftime("%Y-%m-%d %H:%M:%S")

        termination_token = "<TRADE_DONE>"

        # Only inject time context. Runtime protocol is now rendered in get_trade_agent_prompt().
        system_prompt_with_time = f"{system_prompt}\n\nCurrent Time (UTC+8): {current_time}"

        # Only expose required tools + tool selector at the start
        if self.tool_routing_enabled:
            initial_tools = list(REQUIRED_TOOL_NAMES)
            if META_TOOL_NAME not in initial_tools:
                initial_tools.append(META_TOOL_NAME)
            self.tools.set_active_tools(initial_tools)
        else:
            # Non-routing mode: expose a fixed default tool set only.
            default_tools = [name for name in DEFAULT_NON_ROUTED_TOOL_NAMES if name in self.tools.tools]
            self.tools.set_active_tools(default_tools)

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

        decision = None
        executed_trades: List[Dict[str, Any]] = []
        tool_call_cache = {}
        tool_call_counts = {}

        for step in range(self.max_steps):
            # Check if we need to remind the agent about remaining steps
            remaining_steps = self.max_steps - step
            request_messages = list(messages)

            if remaining_steps < AgentConfig.STEP_REMINDER_THRESHOLD:
                logger.info(f"Adding step reminder (Remaining: {remaining_steps})")
                reminder_text = (
                    f"Reminder: You have {remaining_steps} steps remaining. "
                    f"You must output {termination_token} before running out of steps."
                )
                request_messages.append({
                    "role": "user",
                    "content": reminder_text,
                })

            # Requirement 1: Log raw LLM request
            llm_logger.info(f"--- Step {step+1}/{self.max_steps} Request ---")
            llm_logger.info(json.dumps(request_messages, ensure_ascii=False, indent=2))

            logger.info(f"Initiating LLM request (Step {step+1})")

            resp = self.llm.call(request_messages, tools=self.tools.openai_tools)

            # Convert to dict preserving provider-specific extra fields (e.g. Gemini thought_signature)
            resp_dict = self.llm.build_assistant_message_dict(resp)
            tool_calls, tool_guard_warnings = LLMClient.apply_tool_call_guardrails(
                getattr(resp, "tool_calls", None),
                model=getattr(self.llm, "model", None),
            )
            if tool_calls:
                resp_dict["tool_calls"] = tool_calls
            else:
                resp_dict.pop("tool_calls", None)
            if tool_guard_warnings:
                logger.warning("Tool-call guardrails triggered: %s", " | ".join(tool_guard_warnings))
                agent_logger.warning("Tool-call guardrails triggered: %s", " | ".join(tool_guard_warnings))

            # Requirement 1: Log raw LLM response
            llm_logger.info(f"--- Step {step+1}/{self.max_steps} Response ---")
            llm_logger.info(json.dumps(resp_dict, ensure_ascii=False, indent=2))

            # Important: Add assistant response to history
            messages.append(resp_dict)
            if on_step:
                on_step(resp_dict)

            content = resp.content

            # Requirement 2: Log LLM output content and tool calls
            agent_logger.info(f"--- Step {step+1} LLM Output ---")
            agent_logger.info(f"Content: {content}")
            if tool_calls:
                agent_logger.info(
                    f"Tool Calls: {json.dumps(LLMClient.tool_calls_to_roundtrip_dicts(tool_calls), ensure_ascii=False)}"
                )

            # 1) 有工具调用：执行工具并把结果回传给模型
            if tool_calls:
                logger.info(f"LLM requested {len(tool_calls)} tool calls")
                tool_messages = []
                for tc in tool_calls:
                    tc_id, name, args_str = LLMClient.tool_call_parts(tc)
                    parse_error = None
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError as e:
                        parse_error = str(e)
                        args = None

                    # Console output (Simple)
                    logger.info(f"Executing tool: {name}")

                    # File output (Detailed)
                    agent_logger.info(f"Executing tool '{name}' with args: {args_str}")

                    if parse_error:
                        logger.warning(f"Invalid tool arguments for {name}: {parse_error}; raw={args_str!r}")
                        result = {
                            "error": f"Invalid tool arguments JSON for '{name}': {parse_error}. Please retry with valid JSON arguments."
                        }
                    elif not isinstance(args, dict):
                        result = {
                            "error": f"Invalid tool arguments for '{name}': expected object, got {type(args).__name__}."
                        }
                    else:
                        cache_key = f"{name}:{json.dumps(args, sort_keys=True)}"
                        # Tool selector updates active tool set dynamically.
                        # Its result must always reflect latest context, so skip cache.
                        should_cache_tool_result = name != META_TOOL_NAME
                        tool_call_counts[cache_key] = tool_call_counts.get(cache_key, 0) + 1
                        dup_count = tool_call_counts[cache_key]
                        dup_limit = getattr(AgentConfig, "TOOL_CALL_DUP_MAX", 5)
                        dup_warn = getattr(AgentConfig, "TOOL_CALL_DUP_WARN", 10)

                        if dup_count > dup_limit:
                            result = {
                                "error": (
                                    f"Refused to execute tool '{name}' with identical parameters "
                                    f"more than {dup_limit} times."
                                )
                            }
                            if dup_count > dup_warn:
                                result["warning"] = (
                                    "Repeated identical tool calls exceeded 10 times. "
                                    "Consider selecting other tools, use select-tools to get more tools according to your need, or changing parameters."
                                )
                        elif should_cache_tool_result and cache_key in tool_call_cache:
                            result = tool_call_cache[cache_key]
                            logger.info(f"Using cached result for tool: {name}")
                            agent_logger.info(f"Using cached result for tool '{name}' with args: {args_str}")
                        else:
                            try:
                                tool = self.tools.get(name)
                                missing = self._missing_required_args(tool, args)
                                if missing:
                                    result = {
                                        "error": (
                                            f"Missing required arguments for '{name}': {', '.join(missing)}. "
                                            "Please retry with all required fields."
                                        )
                                    }
                                else:
                                    result = self._invoke_llm_tool(
                                        name,
                                        args,
                                        tool_call_id=tc_id,
                                        decision_round_id=decision_round_id,
                                    )
                                    if should_cache_tool_result:
                                        tool_call_cache[cache_key] = result
                                    # Meta tool handling, optional for special tools
                                    if name == META_TOOL_NAME and isinstance(result, dict):
                                        llm_trace = result.pop("_llm_trace", None)
                                        if llm_trace and on_step:
                                            on_step(
                                                {
                                                    "role": "llm_trace",
                                                    "content": json.dumps(llm_trace, ensure_ascii=False),
                                                }
                                            )
                                try:
                                    tool_output_logger.info(
                                        json.dumps(
                                            {"name": name, "args": args, "result": result},
                                            ensure_ascii=False,
                                        )
                                    )
                                except Exception:
                                    tool_output_logger.info(f"Tool result logged for {name}")
                            except Exception as tool_err:
                                # Never abort the whole run because one tool call fails.
                                logger.error(f"Tool execution failed for {name}: {tool_err}")
                                result = {"error": f"Tool execution failed: {str(tool_err)}"}

                    # Log tool result
                    agent_logger.info(f"Tool '{name}' result: {json.dumps(result, ensure_ascii=False)}")
                    if name == "execute_trade":
                        executed_trades.append(result if isinstance(result, dict) else {"raw_result": str(result)})

                    model_result = self._sanitize_tool_result_for_model(name, result)
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": name,
                            "content": json.dumps(model_result, ensure_ascii=False),
                            "_trace_content": json.dumps(result, ensure_ascii=False),
                        }
                    )

                # Append tool outputs only after the whole batch completes
                for tool_msg in tool_messages:
                    model_msg = {
                        "role": tool_msg["role"],
                        "tool_call_id": tool_msg["tool_call_id"],
                        "name": tool_msg["name"],
                        "content": tool_msg["content"],
                    }
                    messages.append(model_msg)
                    if on_step:
                        trace_tool_msg = dict(model_msg)
                        trace_tool_msg["content"] = tool_msg["_trace_content"]
                        on_step(trace_tool_msg)
                if tool_guard_warnings:
                    warn_msg = LLMClient.tool_guardrail_warning_user_message(tool_guard_warnings)
                    messages.append(warn_msg)
                    if on_step:
                        on_step(dict(warn_msg))
                if self.llm.is_gemini_model():
                    messages.append(LLMClient.gemini_post_tool_user_message())
                continue

            # 2) 没有工具调用，按协议处理最终输出
            text_content = content or ""
            if self._is_trade_done_message(text_content):
                # Only write fallback decision if no execute_trade was called
                if not executed_trades:
                    decision = {
                        "operation": "hold",
                        "symbol": "",
                        "direction": "long",
                        "target_portion_of_balance": 0.0,
                        "leverage": 1,
                        "reason": f"Tool-mode terminated by token {termination_token}",
                        "protocol": "tool",
                        "executed_trades": executed_trades,
                    }
                else:
                    # execute_trade was called, don't write duplicate decision
                    decision = {
                        "operation": "hold",  # placeholder, won't be logged
                        "symbol": "",
                        "direction": "long",
                        "target_portion_of_balance": 0.0,
                        "leverage": 1,
                        "reason": "",
                        "protocol": "tool",
                        "executed_trades": executed_trades,
                        "skip_logging": True,  # signal to skip AIDecisionLog
                    }
                logger.info(
                    f"Agent terminated tool-mode loop with token. executed_trade_calls={len(executed_trades)}"
                )
                agent_logger.info(f"Tool-mode final summary: {json.dumps(decision, ensure_ascii=False)}")
                break

            if not text_content:
                error_msg = "LLM returned empty content and no tool calls"
                logger.warning(error_msg)
                agent_logger.warning(error_msg)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was empty. "
                            f"Please continue by either calling the next tool or outputting {termination_token}."
                        ),
                    }
                )
                continue

            logger.info("Tool mode: waiting for termination token, continue next step.")
            continue

        # If loop finished without break (max steps reached)
        if decision is None:
            # 超过 max_steps 还没给出最终决策，保守 hold
            logger.warning("Agent exceeded max steps, fallback to HOLD")
            agent_logger.warning("Exceeded max steps, returning fallback HOLD decision")
            decision = {
                "operation": "hold",
                "symbol": "",
                "direction": "long",
                "target_portion_of_balance": 0.0,
                "leverage": 1,
                "reason": "max_steps reached, fallback hold",
            }
            decision["protocol"] = "tool"
            decision["executed_trades"] = executed_trades

        return decision
