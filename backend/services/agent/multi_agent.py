import json
import logging
import uuid
import inspect
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone, timedelta

from .base import BaseAgent
from .llm_client import LLMClient
from .tools import ToolRegistry
from config.agent_config import AgentConfig
from .prompts.multi_agent_prompts import (
    MANAGER_PROMPT,
    TRADING_AGENT_PROMPT,
    NEWS_AGENT_PROMPT,
    CODER_AGENT_PROMPT
)

logger = logging.getLogger(__name__)

class MultiAgent(BaseAgent):
    """
    A Multi-Agent architecture where a Manager agent coordinates
    specialized sub-agents (Trading, News, Coder).
    """
    def __init__(self, llm: LLMClient, tools: ToolRegistry, max_steps: int = 15, user_id: str = None):
        super().__init__(llm, tools)
        self.max_steps = max_steps
        self.user_id = user_id
        # Memory tools are now registered in env_wrapper.register_default_tools()

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

    def _run_sub_agent(self, agent_name: str, instruction: str, portfolio: Dict, prices: Dict, on_step: Optional[Callable] = None) -> str:
        """Run a single turn for a sub-agent"""
        
        # Select prompt and tools based on agent name
        if agent_name == "TradingAgent":
            system_prompt = TRADING_AGENT_PROMPT
            # Give Trading Agent access to market/account tools
            allowed_tools = ["get_market_snapshot", "get_kline_history", "get_account_state"]
        elif agent_name == "NewsAgent":
            system_prompt = NEWS_AGENT_PROMPT
            # Give News Agent access to search tools
            allowed_tools = ["consult_search_agent"]
        elif agent_name == "CoderAgent":
            system_prompt = CODER_AGENT_PROMPT
            # Give Coder Agent access to coding/file tools
            allowed_tools = ["run_python_script", "read_file", "write_file", "execute_shell_command"]
        else:
            return f"Error: Unknown agent {agent_name}"
            
        # Ensure we don't try to access tools that aren't available in the registry
        available_tools_names = [t["function"]["name"] for t in self.tools.openai_tools]
        valid_tools = [t for t in allowed_tools if t in available_tools_names]

        # Format prompt
        formatted_prompt = system_prompt.format(
            instruction=instruction,
            portfolio=json.dumps(portfolio, ensure_ascii=False),
            prices=json.dumps(prices, ensure_ascii=False)
        )

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
            tool_calls = resp.tool_calls
            
            # Add to local messages for continuity
            if hasattr(resp, "model_dump"):
                resp_dict = resp.model_dump()
            else:
                resp_dict = resp.dict()
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
                    "tool_calls": [t.model_dump() if hasattr(t, "model_dump") else t for t in tool_calls] if tool_calls else None,
                    "metadata": {"agent": agent_name}
                }
                on_step(step_data)

            if tool_calls:
                for tc in tool_calls:
                    name = tc.function.name
                    args_str = tc.function.arguments or "{}"
                    try:
                        args = json.loads(args_str)
                    except json.JSONDecodeError as e:
                        result = {
                            "error": f"Invalid tool arguments JSON for '{name}': {e}. Please retry with valid JSON arguments."
                        }
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": name,
                            "content": json.dumps(result, ensure_ascii=False)
                        }
                        messages.append(tool_msg)
                        if on_step:
                            on_step({
                                "role": "tool",
                                "tool_call_id": tc.id,
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
                                result = tool_func(**args)
                    except Exception as tool_err:
                        logger.error(f"Sub-agent tool execution failed for {name}: {tool_err}")
                        result = {"error": f"Tool execution failed: {str(tool_err)}"}
                    
                    # Add tool result to messages
                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False)
                    }
                    messages.append(tool_msg)
                    
                    if on_step:
                        on_step({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": name,
                            "content": json.dumps(result, ensure_ascii=False),
                            "metadata": {"agent": agent_name}
                        })
            else:
                # No tool calls, this is the final answer from sub-agent
                current_response = msg_content
                break
        
        return current_response

    def run(self, portfolio: Dict[str, Any], prices: Dict[str, float], on_step: Optional[Callable[[Dict], None]] = None, trace_id: Optional[str] = None) -> Dict[str, Any]:
        
        logger.info("Starting Multi-Agent decision process")

        # Main Manager Loop
        self.context = [] # Clear context
            
        final_decision = None
        
        for step in range(self.max_steps):
            # Construct Manager Prompt
            # We summarize the conversation history (context) into the prompt
            context_str = "\n".join(self.context)
            
            formatted_manager_prompt = MANAGER_PROMPT.format(
                context=context_str if context_str else "No prior actions.",
                portfolio=json.dumps(portfolio, ensure_ascii=False),
                prices=json.dumps(prices, ensure_ascii=False)
            )
            
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
                    result = self._run_sub_agent(agent_name, instruction, portfolio, prices, on_step)
                    
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
                "reason": "MultiAgent Manager did not reach a conclusion within max steps."
            }

        return final_decision

