import json
import logging
import math
import uuid
import inspect
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone, timedelta

from benchmark.builtin.prompts import get_prompt_resolver, require_profile_contract

from .base import BaseAgent
from .llm_client import LLMClient
from .tools import ToolRegistry

logger = logging.getLogger(__name__)

MULTI_AGENT_FINAL_TOOL_CALL_ID = "multi-agent-final"
MULTI_AGENT_TRADE_OPERATIONS = frozenset({"open", "close", "all_in", "close_all"})


def _absent(value: Any) -> bool:
    return value in (None, "")


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def manager_close_ratio(decision: Dict[str, Any]) -> tuple[Any, str | None]:
    """Map Manager close sizing to execute_trade close_ratio.

    Missing close_ratio with missing or 0 remaining target is a full close.
    An explicit non-positive, non-finite, or >1 close_ratio is rejected.
    """
    raw_close = decision.get("close_ratio")
    if not _absent(raw_close):
        parsed = _finite_number(raw_close)
        if parsed is None or parsed <= 0 or parsed > 1:
            return None, "close_ratio must be in (0, 1]"
        return raw_close, None

    raw_portion = decision.get("target_portion_of_balance")
    if _absent(raw_portion):
        return 1, None
    parsed = _finite_number(raw_portion)
    if parsed is None or parsed < 0 or parsed > 1:
        return None, "target_portion_of_balance must be in [0, 1]"
    if parsed == 0:
        return 1, None
    return raw_portion, None


class MultiAgent(BaseAgent):
    """
    A Multi-Agent architecture where a Manager agent coordinates
    specialized sub-agents (Trading, News, Coder).
    """

    PROMPT_PROFILE_ID = "core.multi-agent.default"

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        max_steps: int = 15,
        user_id: str = None,
        agent_name: Optional[str] = None,
        prompt_resolver=None,
    ):
        super().__init__(llm, tools, agent_name=agent_name)
        self.max_steps = max_steps
        self.user_id = user_id
        self.prompt_resolver = get_prompt_resolver(prompt_resolver)
        require_profile_contract(
            self.prompt_resolver,
            self.PROMPT_PROFILE_ID,
            "multi_agent",
        )

        # Shared conversation history (context)
        self.context = []

    def _missing_required_args(self, func: Callable, args: Dict[str, Any]) -> List[str]:
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

    def _run_sub_agent(
        self,
        agent_name: str,
        instruction: str,
        portfolio: Dict,
        prices: Dict,
        on_step: Optional[Callable] = None,
        decision_round_id: Optional[str] = None,
    ) -> str:
        """Run a single turn for a sub-agent"""
        
        # Select prompt and tools based on agent name
        if agent_name == "TradingAgent":
            slot = "trading"
            prompt_variables = {
                "instruction": instruction,
                "portfolio": json.dumps(portfolio, ensure_ascii=False),
                "prices": json.dumps(prices, ensure_ascii=False),
            }
            # Give Trading Agent access to market/account tools
            allowed_tools = ["get_market_snapshot", "get_kline_history", "get_account_state"]
        elif agent_name == "NewsAgent":
            slot = "news"
            prompt_variables = {"instruction": instruction}
            # Give News Agent access to search tools
            allowed_tools = ["consult_search_agent"]
        elif agent_name == "CoderAgent":
            slot = "coder"
            prompt_variables = {"instruction": instruction}
            # Give Coder Agent access to coding/file tools
            allowed_tools = ["run_python_script", "read_file", "write_file", "execute_shell_command"]
        else:
            return f"Error: Unknown agent {agent_name}"
            
        # Ensure we don't try to access tools that aren't available in the registry
        available_tools_names = [t["function"]["name"] for t in self.tools.openai_tools]
        valid_tools = [t for t in allowed_tools if t in available_tools_names]

        formatted_prompt = self.prompt_resolver.render_slot(
            self.PROMPT_PROFILE_ID,
            slot,
            prompt_variables,
        ).content

        messages = [
            {"role": "system", "content": formatted_prompt},
            # We can optionally feed some recent context here if needed, 
            # but usually the instruction contains what's needed.
        ]

        # Log start of sub-agent
        if on_step:
            on_step({
                "role": "system", 
                "content": f"Manager delegated to {agent_name}: {instruction}",
                "metadata": {"agent": "Manager"}
            })

        # Run LLM for sub-agent (Single turn or small loop? Let's do single turn with tools support)
        # For simplicity in this iteration, we allow the sub-agent to use tools in a mini-ReAct loop or just one pass.
        # Let's give it a few steps to use tools.
        sub_agent_steps = 20
        
        # Filter tools
        agent_tools = [t for t in self.tools.openai_tools if t["function"]["name"] in valid_tools]
        
        current_response = ""
        
        for _ in range(sub_agent_steps):
            resp = self.llm.call(messages, tools=agent_tools if agent_tools else None)
            
            # Log response
            msg_content = resp.content or ""
            tool_calls, tool_guard_warnings = LLMClient.apply_tool_call_guardrails(
                getattr(resp, "tool_calls", None),
                model=getattr(self.llm, "model", None),
            )
            if tool_guard_warnings:
                logger.warning("Tool-call guardrails triggered: %s", " | ".join(tool_guard_warnings))
            
            # Add to local messages for continuity
            resp_dict = self.llm.build_assistant_message_dict(resp)
            if tool_calls:
                resp_dict["tool_calls"] = tool_calls
            else:
                resp_dict.pop("tool_calls", None)
            messages.append(resp_dict)
            
            # Emit step for UI with specific role/color
            # Map agent name to a role string that UI can potentially use or just use "assistant" but prefix content
            if on_step:
                display_role = "assistant" # Standard role
                # We can encode the agent name in the content or metadata
                # UI currently doesn't support custom roles well, so we use standard ones
                # but we can prefix the content.
                
                step_data = {
                    "role": display_role,
                    "content": f"[{agent_name}] {msg_content}" if msg_content else None,
                    "tool_calls": LLMClient.tool_calls_to_roundtrip_dicts(tool_calls),
                    "metadata": {"agent": agent_name}
                }
                on_step(step_data)

            if tool_calls:
                for tc in tool_calls:
                    tc_id, name, args_str = LLMClient.tool_call_parts(tc)
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError as e:
                        result = {
                            "error": f"Invalid tool arguments JSON for '{name}': {e}. Please retry with valid JSON arguments."
                        }
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": name,
                            "content": json.dumps(result, ensure_ascii=False)
                        }
                        messages.append(tool_msg)
                        if on_step:
                            on_step({
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "name": name,
                                "content": json.dumps(result, ensure_ascii=False),
                                "metadata": {"agent": agent_name}
                            })
                        continue
                    
                    # Execute tool
                    try:
                        if not isinstance(args, dict):
                            result = {
                                "error": f"Invalid tool arguments for '{name}': expected object, got {type(args).__name__}."
                            }
                        else:
                            tool_func = self.tools.get(name)
                            missing = self._missing_required_args(tool_func, args)
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
                    except Exception as tool_err:
                        logger.error(f"Sub-agent tool execution failed for {name}: {tool_err}")
                        result = {"error": f"Tool execution failed: {str(tool_err)}"}
                    
                    # Add tool result to messages
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False)
                    }
                    messages.append(tool_msg)
                    
                    if on_step:
                        on_step({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": name,
                            "content": json.dumps(result, ensure_ascii=False),
                            "metadata": {"agent": agent_name}
                        })
                if tool_guard_warnings:
                    messages.append(LLMClient.tool_guardrail_warning_user_message(tool_guard_warnings))
                if self.llm.is_gemini_model():
                    messages.append(LLMClient.gemini_post_tool_user_message())
            else:
                # No tool calls, this is the final answer from sub-agent
                current_response = msg_content
                break
        
        return current_response

    def _has_execute_trade(self) -> bool:
        try:
            self.tools.get("execute_trade")
        except KeyError:
            return False
        return True

    def _final_trade_arguments(
        self, decision: Dict[str, Any]
    ) -> tuple[Dict[str, Any], Dict[str, Any] | None]:
        operation = str(decision.get("operation") or "").strip().lower()
        arguments: Dict[str, Any] = {"operation": operation}
        symbol = decision.get("symbol")
        if symbol not in (None, ""):
            arguments["symbol"] = str(symbol).strip().upper()
        direction = decision.get("direction")
        if direction not in (None, ""):
            arguments["direction"] = str(direction).strip().lower()
        market = decision.get("market")
        if market not in (None, ""):
            arguments["market"] = str(market).strip().upper()
        elif arguments.get("symbol"):
            arguments["market"] = "CRYPTO"
        leverage = decision.get("leverage")
        if leverage not in (None, ""):
            arguments["leverage"] = leverage
        portion = decision.get("target_portion_of_balance")
        usd_amount = decision.get("usd_amount")
        if usd_amount not in (None, ""):
            arguments["usd_amount"] = usd_amount
        size_mode = decision.get("size_mode")
        if size_mode not in (None, ""):
            arguments["size_mode"] = size_mode
        reject: Dict[str, Any] | None = None
        if operation == "close":
            size_mode_norm = str(size_mode or "").strip().lower()
            if size_mode_norm == "usd":
                parsed_usd = None if _absent(usd_amount) else _finite_number(usd_amount)
                if parsed_usd is None or parsed_usd <= 0:
                    reject = {
                        "executed": False,
                        "error": (
                            "usd_amount must be a positive finite number "
                            "when size_mode is usd"
                        ),
                        "reject_code": "SIZING_VALUE_INVALID",
                        "operation": operation,
                        "symbol": arguments.get("symbol") or "",
                        "market": arguments.get("market") or "CRYPTO",
                    }
            else:
                ratio, error = manager_close_ratio(decision)
                if error is not None:
                    reject = {
                        "executed": False,
                        "error": error,
                        "reject_code": "SIZING_VALUE_INVALID",
                        "operation": operation,
                        "symbol": arguments.get("symbol") or "",
                        "market": arguments.get("market") or "CRYPTO",
                    }
                else:
                    arguments["close_ratio"] = ratio
        elif portion not in (None, ""):
            arguments["target_portion_of_balance"] = portion
        reason = decision.get("reason")
        if reason not in (None, ""):
            arguments["reason"] = reason
        return arguments, reject

    def _execute_final_decision(
        self,
        final_decision: Dict[str, Any],
        *,
        on_step: Optional[Callable] = None,
        decision_round_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(final_decision, dict):
            return final_decision
        operation = str(final_decision.get("operation") or "").strip().lower()
        if operation not in MULTI_AGENT_TRADE_OPERATIONS:
            return final_decision

        arguments, reject = self._final_trade_arguments(final_decision)
        tool_call_id = MULTI_AGENT_FINAL_TOOL_CALL_ID
        if reject is not None:
            result = reject
        elif self._has_execute_trade():
            result = self._invoke_llm_tool(
                "execute_trade",
                arguments,
                tool_call_id=tool_call_id,
                decision_round_id=decision_round_id,
            )
        else:
            result = {
                "executed": False,
                "error": "execute_trade is not available for the Multi-Agent final decision",
                "reject_code": "TOOL_UNAVAILABLE",
                "operation": arguments["operation"],
                "symbol": arguments.get("symbol") or "",
                "market": arguments.get("market") or "CRYPTO",
            }

        if isinstance(result, dict):
            payload = dict(result)
            payload.setdefault("operation", arguments["operation"])
            if arguments.get("symbol"):
                payload.setdefault("symbol", arguments["symbol"])
            payload.setdefault("market", arguments.get("market") or "CRYPTO")
        else:
            payload = {
                "raw_result": str(result),
                "operation": arguments["operation"],
                "symbol": arguments.get("symbol") or "",
                "market": arguments.get("market") or "CRYPTO",
            }

        if on_step:
            on_step(
                {
                    "role": "assistant",
                    "content": (
                        "[Manager] Executing final decision via execute_trade: "
                        f"{arguments['operation']} {arguments.get('symbol') or ''}"
                    ).strip(),
                    "tool_calls": [
                        {
                            "id": tool_call_id,
                            "type": "function",
                            "function": {
                                "name": "execute_trade",
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                    ],
                    "metadata": {"agent": "Manager"},
                }
            )
            on_step(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": "execute_trade",
                    "content": json.dumps(payload, ensure_ascii=False),
                    "metadata": {"agent": "Manager"},
                }
            )

        executed = dict(final_decision)
        executed["protocol"] = "tool"
        executed["executed_trades"] = [payload]
        return executed

    def run(
        self,
        portfolio: Dict[str, Any],
        prices: Dict[str, float],
        on_step: Optional[Callable[[Dict], None]] = None,
        trace_id: Optional[str] = None,
        decision_round_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        
        logger.info("Starting Multi-Agent decision process")

        # Main Manager Loop
        self.context = [] # Clear context
            
        final_decision = None
        
        for step in range(self.max_steps):
            # Construct Manager Prompt
            # We summarize the conversation history (context) into the prompt
            context_str = "\n".join(self.context)
            
            formatted_manager_prompt = self.prompt_resolver.render_slot(
                self.PROMPT_PROFILE_ID,
                "manager",
                {
                    "context": context_str if context_str else "No prior actions.",
                    "portfolio": json.dumps(portfolio, ensure_ascii=False),
                    "prices": json.dumps(prices, ensure_ascii=False),
                },
            ).content
            
            # Call Manager (LLM)
            # Manager has NO tools, only decides next action
            messages = [{"role": "system", "content": formatted_manager_prompt}]
            
            resp = self.llm.call(messages) # No tools
            content = resp.content
            
            # Log Manager Thought
            if on_step:
                on_step({
                    "role": "assistant",
                    "content": f"[Manager] {content}",
                    "metadata": {"agent": "Manager"}
                })
            
            # Parse Manager Output
            try:
                # Cleanup potential markdown json
                json_str = content
                if "```json" in content:
                    json_str = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    json_str = content.split("```")[1].split("```")[0]
                
                manager_decision = json.loads(json_str.strip())
                
                action = manager_decision.get("next_action")
                
                if action == "call_agent":
                    agent_name = manager_decision.get("agent_name")
                    instruction = manager_decision.get("instruction")
                    reason = manager_decision.get("reason")
                    
                    self.context.append(f"Step {step+1}: Manager decided to call {agent_name}. Reason: {reason}")
                    
                    # Execute Sub-agent
                    result = self._run_sub_agent(
                        agent_name,
                        instruction,
                        portfolio,
                        prices,
                        on_step,
                        decision_round_id,
                    )
                    
                    self.context.append(f"Result from {agent_name}: {result}")
                    
                elif action == "finish":
                    final_decision = manager_decision.get("final_decision")
                    if final_decision:
                        final_decision["reason"] = f"[MultiAgent] {final_decision.get('reason', '')}"
                        break
                else:
                    self.context.append(f"Step {step+1}: Manager returned unknown action {action}")
                    
            except json.JSONDecodeError:
                self.context.append(f"Step {step+1}: Manager output invalid JSON. Content: {content}")
                logger.warning(f"Manager output invalid JSON: {content}")
            except Exception as e:
                logger.error(f"Error in manager loop: {e}")
                self.context.append(f"Step {step+1}: Error: {e}")

        # Fallback if no decision
        if not final_decision:
            final_decision = {
                "operation": "hold",
                "symbol": "",
                "direction": "long",
                "target_portion_of_balance": 0.0,
                "leverage": 1,
                "reason": "MultiAgent Manager did not reach a conclusion within max steps.",
            }

        return self._execute_final_decision(
            final_decision,
            on_step=on_step,
            decision_round_id=decision_round_id,
        )
