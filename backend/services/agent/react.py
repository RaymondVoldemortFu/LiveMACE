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


# SYSTEM_PROMPT = TRADE_AGENT_PROMPT
GEMINI_COMPAT_INSTRUCTION = """

========================
GEMINI COMPATIBILITY MODE
========================
You are running in Gemini compatibility mode.
Do NOT use native function calling.

Use normal reasoning text. If you need a tool, append exactly one tool command block:
<CALL_TOOL>
{"tool_name":"exact_tool_name","arguments":{...}}
</CALL_TOOL>

Rules:
- Keep at most one <CALL_TOOL> block per response.
- `tool_name` must exactly match one of the currently available tools.
- Use `select_tools` whenever you need the router to update the available tool set.
- Final decision must still use <FINAL_JSON>...</FINAL_JSON>.
"""

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
        # Memory tools are now registered in env_wrapper.register_default_tools()
        # self.memory = get_memory_service()
        ensure_tool_selector_tool(self.llm, self.tools)

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

    def _available_tools_text(self) -> str:
        lines: List[str] = []
        for tool_name in self.tools.active_tool_names or []:
            if tool_name not in self.tools.tools:
                continue
            tool = self.tools.get(tool_name)
            description = (tool.description or "").strip()
            lines.append(f"- {tool.name}: {description}" if description else f"- {tool.name}")
        return "\n".join(lines) if lines else "- [no active tools]"

    def _active_tool_names(self) -> List[str]:
        return [name for name in (self.tools.active_tool_names or []) if name in self.tools.tools]

    def _build_gemini_system_prompt(self, base_prompt: str) -> str:
        return (
            f"{base_prompt}\n"
            f"{GEMINI_COMPAT_INSTRUCTION}\n\n"
            "Currently available tools:\n"
            f"{self._available_tools_text()}"
        )

    def _normalize_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        if "leverage" not in decision or not decision["leverage"]:
            decision["leverage"] = 1
        if "direction" not in decision or not decision["direction"]:
            decision["direction"] = "long"
        else:
            decision["direction"] = str(decision["direction"]).lower()
        return decision

    def _extract_call_tool_command(self, content: str) -> Optional[Dict[str, Any]]:
        if not content:
            return None
        match = re.search(r"<CALL_TOOL>\s*(\{.*?\})\s*</CALL_TOOL>", content, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(1))
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _run_gemini_compatible(
        self,
        system_prompt_with_time: str,
        portfolio: Dict[str, Any],
        prices: Dict[str, float],
        on_step: Optional[Callable[[Dict], None]] = None,
    ) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._build_gemini_system_prompt(system_prompt_with_time)},
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

        for step in range(self.max_steps):
            request_messages = list(messages)
            request_messages.append(
                {
                    "role": "user",
                    "content": (
                        "Available tools for this step:\n"
                        f"{self._available_tools_text()}\n\n"
                        "If you need a tool, append one <CALL_TOOL>{...}</CALL_TOOL> block. "
                        "If you can finish, output <FINAL_JSON>...</FINAL_JSON>."
                    ),
                }
            )

            llm_logger.info(f"--- Gemini Step {step+1}/{self.max_steps} Request ---")
            llm_logger.info(json.dumps(request_messages, ensure_ascii=False, indent=2))
            logger.info(f"Initiating Gemini compatibility request (Step {step+1})")

            resp = self.llm.call(request_messages, tools=None)
            content = self.llm.extract_text_content(resp)
            resp_dict = {
                "role": "assistant",
                "content": content,
            }

            llm_logger.info(f"--- Gemini Step {step+1}/{self.max_steps} Response ---")
            llm_logger.info(json.dumps(resp_dict, ensure_ascii=False, indent=2))

            messages.append(resp_dict)
            if on_step:
                on_step(resp_dict)

            agent_logger.info(f"--- Gemini Step {step+1} Output ---")
            agent_logger.info(f"Content: {content}")

            if not content:
                warning_msg = "Gemini compatibility mode returned empty content; continuing with recovery prompt"
                logger.warning(warning_msg)
                agent_logger.warning(warning_msg)
                messages.append({
                    "role": "user",
                    "content": "Your previous response was empty. Please continue with reasoning, a <CALL_TOOL> block, or <FINAL_JSON>.",
                })
                continue

            if content:
                match = re.search(r"<FINAL_JSON>(.*?)</FINAL_JSON>", content, re.DOTALL)
                if match:
                    json_str = match.group(1).strip()
                    try:
                        decision = self._normalize_decision(json.loads(json_str))
                        logger.info(f"Gemini compatibility final decision: {decision.get('operation')} {decision.get('symbol', '')}")
                        agent_logger.info(f"Final Decision Parsed: {json.dumps(decision, ensure_ascii=False)}")
                        break
                    except json.JSONDecodeError:
                        messages.append({
                            "role": "user",
                            "content": "Your <FINAL_JSON> block was invalid JSON. Please retry with valid JSON.",
                        })
                        continue

            command = self._extract_call_tool_command(content)
            if command:
                name = command.get("tool_name")
                args = command.get("arguments")
                active_tool_names = self._active_tool_names()

                if not isinstance(name, str) or name not in active_tool_names:
                    result = {"error": "Invalid or unavailable tool_name. Please choose one of the currently available tools."}
                elif not isinstance(args, dict):
                    result = {"error": f"Invalid tool arguments for '{name}': expected object."}
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
                            result = tool(**args)
                        try:
                            tool_output_logger.info(
                                json.dumps(
                                    {"name": name, "args": args, "result": result, "mode": "gemini_compat"},
                                    ensure_ascii=False,
                                )
                            )
                        except Exception:
                            tool_output_logger.info(f"Tool result logged for {name}")
                    except Exception as tool_err:
                        logger.error(f"Gemini compatibility tool execution failed for {name}: {tool_err}")
                        result = {"error": f"Tool execution failed: {str(tool_err)}"}

                model_result = self._sanitize_tool_result_for_model(name or "", result)
                tool_msg = {
                    "role": "tool",
                    "name": name or "unknown_tool",
                    "content": json.dumps(model_result, ensure_ascii=False),
                }
                messages.append(tool_msg)
                if on_step:
                    on_step({
                        "role": "assistant",
                        "content": content,
                        "tool_calls": [
                            {
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(args or {}, ensure_ascii=False),
                                }
                            }
                        ],
                    })
                    trace_tool_msg = dict(tool_msg)
                    trace_tool_msg["content"] = json.dumps(result, ensure_ascii=False)
                    on_step(trace_tool_msg)
                continue

            # Plain reasoning step without tool command is allowed.
            continue

        if decision is None:
            logger.warning("Gemini compatibility mode exceeded max steps, fallback to HOLD")
            agent_logger.warning("Gemini compatibility mode exceeded max steps, returning fallback HOLD decision")
            decision = {
                "operation": "hold",
                "symbol": "",
                "direction": "long",
                "target_portion_of_balance": 0.0,
                "leverage": 1,
                "reason": "max_steps reached in Gemini compatibility mode, fallback hold",
            }

        return decision, messages

    def run(self, portfolio: Dict[str, Any], prices: Dict[str, float], on_step: Optional[Callable[[Dict], None]] = None, trace_id: Optional[str] = None) -> Dict[str, Any]:
        """
        输入:
            portfolio: 和原 call_ai_for_decision 中一致的结构
            prices: symbol -> price 的字典
            on_step: Optional callback function called after each step with the message dict
            trace_id: Trace ID for current session
        输出:
            与原先 call_ai_for_decision 返回值同结构的决策 dict
        """
        # Log start of decision process
        logger.info("Starting agent decision process (ReAct Architecture)")
        agent_logger.info("=== Starting New Decision Process (ReAct) ===")
        agent_logger.info(f"Portfolio: {json.dumps(portfolio, ensure_ascii=False)}")
        agent_logger.info(f"Prices: {json.dumps(prices, ensure_ascii=False)}")

        # Check if memory tools are available
        has_memory = any(tool.name in ['memory_add', 'memory_search'] for tool in self.tools.tools.values())
        system_prompt = get_trade_agent_prompt(memory_enabled=has_memory)

        # Get current UTC+8 time
        tz_utc_8 = timezone(timedelta(hours=8))
        current_time = now_in_tz(tz_utc_8).strftime("%Y-%m-%d %H:%M:%S")

        decision_protocol = (getattr(AgentConfig, "TRADE_DECISION_PROTOCOL", "tool") or "tool").strip().lower()
        termination_token = getattr(AgentConfig, "AGENT_TRADE_TERMINATION_TOKEN", "<TRADE_DONE>")

        # Add time context and runtime protocol to system prompt
        system_prompt_with_time = f"{system_prompt}\n\nCurrent Time (UTC+8): {current_time}"
        if decision_protocol == "tool":
            system_prompt_with_time += (
                "\n\nRuntime Protocol: TOOL MODE (default)\n"
                "You MUST execute real trading actions via the execute_trade tool.\n"
                "You may call execute_trade multiple times.\n"
                f"When done, output ONLY this exact token: {termination_token}\n"
                "Do NOT output <FINAL_JSON> in TOOL MODE.\n"
            )
        else:
            system_prompt_with_time += (
                "\n\nRuntime Protocol: LEGACY FINAL_JSON MODE\n"
                "You must output final decision wrapped by <FINAL_JSON>...</FINAL_JSON>.\n"
            )

        # Only expose required tools + tool selector at the start
        initial_tools = list(REQUIRED_TOOL_NAMES)
        if META_TOOL_NAME not in initial_tools:
            initial_tools.append(META_TOOL_NAME)
        self.tools.set_active_tools(initial_tools)

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

        if self.llm.is_gemini_model():
            decision, _ = self._run_gemini_compatible(
                system_prompt_with_time=system_prompt_with_time,
                portfolio=portfolio,
                prices=prices,
                on_step=on_step,
            )
            return decision

        for step in range(self.max_steps):
            # Check if we need to remind the agent about remaining steps
            remaining_steps = self.max_steps - step
            request_messages = list(messages)

            if remaining_steps < AgentConfig.STEP_REMINDER_THRESHOLD:
                logger.info(f"Adding step reminder (Remaining: {remaining_steps})")
                reminder_text = (
                    f"Reminder: You have {remaining_steps} steps remaining. "
                    f"You must output {termination_token} before running out of steps."
                    if decision_protocol == "tool"
                    else f"Reminder: You have {remaining_steps} steps remaining. You must output <FINAL_JSON> before running out of steps."
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
                agent_logger.info(
                    f"Tool Calls: {json.dumps([t.model_dump() if hasattr(t, 'model_dump') else str(t) for t in tool_calls], ensure_ascii=False)}"
                )

            # 1) 有工具调用：执行工具并把结果回传给模型
            if tool_calls:
                logger.info(f"LLM requested {len(tool_calls)} tool calls")
                tool_messages = []
                for tc in tool_calls:
                    name = tc.function.name
                    args_str = tc.function.arguments or "{}"
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
                        elif cache_key in tool_call_cache:
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
                                    result = tool(**args)
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
                            "tool_call_id": tc.id,
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
                continue

            # 2) 没有工具调用，按协议处理最终输出
            text_content = content or ""
            if decision_protocol == "tool":
                if text_content and text_content.strip() == termination_token:
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
                    logger.info(
                        f"Agent terminated tool-mode loop with token. executed_trade_calls={len(executed_trades)}"
                    )
                    agent_logger.info(f"Tool-mode final summary: {json.dumps(decision, ensure_ascii=False)}")
                    break

                if not text_content:
                    error_msg = "LLM returned empty content and no tool calls in tool mode"
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

            # Legacy FINAL_JSON mode
            if text_content:
                match = re.search(r"<FINAL_JSON>(.*?)</FINAL_JSON>", text_content, re.DOTALL)
                if match:
                    json_str = match.group(1).strip()
                    try:
                        decision = json.loads(json_str)

                        logger.info(f"Agent reached final decision: {decision.get('operation')} {decision.get('symbol', '')}")
                        agent_logger.info(f"Final Decision Parsed: {json.dumps(decision, ensure_ascii=False)}")

                        # Keep compatibility with previous decision handling
                        if "leverage" not in decision or not decision["leverage"]:
                            decision["leverage"] = 1
                        if "direction" not in decision or not decision["direction"]:
                            decision["direction"] = "long"
                        else:
                            decision["direction"] = decision["direction"].lower()
                        break
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse decision JSON within <FINAL_JSON>: {e}. Returning fallback HOLD.")
                        agent_logger.error(f"JSON Parse Error in <FINAL_JSON>: {e}. Content: {json_str}")
                        decision = {
                            "operation": "hold",
                            "symbol": "",
                            "direction": "long",
                            "target_portion_of_balance": 0.0,
                            "leverage": 1,
                            "reason": "JSON Parse Error in <FINAL_JSON>, fallback hold",
                        }
                        break
            else:
                error_msg = "LLM returned empty content and no tool calls"
                logger.error(error_msg)
                agent_logger.error(error_msg)
                raise ValueError(error_msg)

            logger.info("No tool calls and no <FINAL_JSON> found. Continuing conversation (thought step).")
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
            if decision_protocol == "tool":
                decision["protocol"] = "tool"
                decision["executed_trades"] = executed_trades

        return decision
