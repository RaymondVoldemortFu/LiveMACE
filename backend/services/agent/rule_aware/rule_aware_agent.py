"""
Rule-Aware Agent - Trading agent with built-in rule compliance
"""
import re
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Callable, Optional

from ..base import BaseAgent
from ..llm_client import LLMClient
from ..tools import ToolRegistry
from ..memory import get_memory_service
from config.agent_config import AgentConfig

from .rule_engine import RuleEngine
from .rule_validator import RuleValidator
from .compliance_auditor import ComplianceAuditor
from .prompts import RULE_AWARE_SYSTEM_PROMPT, RULE_AWARE_REMINDER_PROMPT

logger = logging.getLogger(__name__)
llm_logger = logging.getLogger("llm_trace")
agent_logger = logging.getLogger("agent_decision")


class RuleAwareAgent(BaseAgent):
    """
    Rule-Aware Trading Agent
    
    This agent is designed to operate under strict rule compliance requirements.
    It explicitly checks rules, documents compliance, and handles rule conflicts.
    """
    
    def __init__(
        self, 
        llm: LLMClient, 
        tools: ToolRegistry, 
        rule_engine: RuleEngine,
        max_steps: int = AgentConfig.MAX_STEPS,
        user_id: str = None
    ):
        """
        Initialize Rule-Aware Agent
        
        Args:
            llm: LLM client
            tools: Tool registry
            rule_engine: Rule engine with loaded rules
            max_steps: Maximum reasoning steps
            user_id: User ID for memory
        """
        super().__init__(llm, tools)
        self.max_steps = max_steps
        self.user_id = user_id
        self.memory = get_memory_service()
        
        # Rule compliance components
        self.rule_engine = rule_engine
        self.rule_validator = RuleValidator(rule_engine)
        self.compliance_auditor = ComplianceAuditor(rule_engine, self.rule_validator)
        
        # Log loaded rules
        rule_summary = self.rule_engine.get_rule_summary()
        logger.info(f"Rule-Aware Agent initialized with rules: {rule_summary}")
    
    def run(
        self, 
        portfolio: Dict[str, Any], 
        prices: Dict[str, float], 
        on_step: Optional[Callable[[Dict], None]] = None,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute rule-aware decision making process
        
        Args:
            portfolio: Current portfolio state
            prices: Market prices
            on_step: Callback for each step
            trace_id: Trace ID for logging
        
        Returns:
            Trading decision with compliance audit
        """
        logger.info("Starting Rule-Aware Agent decision process")
        agent_logger.info("=== Starting Rule-Aware Decision Process ===")
        agent_logger.info(f"Portfolio: {json.dumps(portfolio, ensure_ascii=False)}")
        agent_logger.info(f"Prices: {json.dumps(prices, ensure_ascii=False)}")
        
        # Get current time
        tz_utc_8 = timezone(timedelta(hours=8))
        current_time = datetime.now(tz_utc_8).strftime("%Y-%m-%d %H:%M:%S")
        
        # Format rules for prompt
        rule_documents = self.rule_engine.format_rules_for_prompt()
        
        # Build system prompt with rules
        system_prompt = RULE_AWARE_SYSTEM_PROMPT.format(
            rule_documents=rule_documents,
            current_time=current_time,
            portfolio=json.dumps(portfolio, ensure_ascii=False, indent=2),
            prices=json.dumps(prices, ensure_ascii=False, indent=2)
        )
        
        # Retrieve memory if available
        if self.memory and self.user_id:
            try:
                query = f"Trading context: {len(portfolio.get('positions', {}))} positions. Market: {list(prices.keys())}"
                retrieved_memories = self.memory.search(query, user_id=self.user_id)
                
                if retrieved_memories:
                    memory_texts = []
                    for m in retrieved_memories:
                        text = m.get('memory') or m.get('text') or m.get('content')
                        if text:
                            memory_texts.append(f"- {text}")
                    
                    if memory_texts:
                        memory_block = "\n".join(memory_texts)
                        system_prompt += f"\n\n## Relevant Memories:\n{memory_block}"
                        agent_logger.info(f"Retrieved memories: {memory_block}")
                        
                        if on_step:
                            on_step({
                                "role": "memory",
                                "content": f"Retrieved Memories:\n{memory_block}",
                                "metadata": {"type": "memory"}
                            })
            except Exception as e:
                logger.error(f"Failed to retrieve memory: {e}")
        
        # Initialize conversation
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "Begin your analysis. Use tools to gather information, check all applicable rules, and provide a compliant trading decision."
            }
        ]
        
        decision = None
        compliance_audit = None
        
        try:
            for step in range(self.max_steps):
                remaining_steps = self.max_steps - step
                
                # Add reminder when running low on steps
                if remaining_steps <= AgentConfig.STEP_REMINDER_THRESHOLD and remaining_steps > 1:
                    reminder = RULE_AWARE_REMINDER_PROMPT.format(remaining_steps=remaining_steps)
                    messages.append({"role": "user", "content": reminder})
                
                # Call LLM
                logger.info(f"Rule-Aware Agent step {step + 1}/{self.max_steps}")
                resp = self.llm.call(messages, tools=self.tools.openai_tools)
                
                msg_content = resp.content or ""
                tool_calls = resp.tool_calls
                
                # Log to llm_trace
                llm_logger.info(f"Step {step + 1} - Assistant response: {msg_content[:500]}...")
                
                # Add assistant message
                if hasattr(resp, "model_dump"):
                    resp_dict = resp.model_dump()
                else:
                    resp_dict = resp.dict()
                messages.append(resp_dict)
                
                # Notify step callback
                if on_step:
                    on_step({
                        "role": "assistant",
                        "content": msg_content,
                        "tool_calls": tool_calls,
                        "metadata": {"step": step + 1}
                    })
                
                # Check for final decision
                if "<FINAL_JSON>" in msg_content and "</FINAL_JSON>" in msg_content:
                    logger.info("Final decision detected in agent output")
                    
                    # Parse the full output for compliance audit
                    parsed_output = self.compliance_auditor.parse_agent_output(msg_content)
                    
                    # Extract JSON decision
                    try:
                        start = msg_content.index("<FINAL_JSON>") + len("<FINAL_JSON>")
                        end = msg_content.index("</FINAL_JSON>")
                        json_str = msg_content[start:end].strip()
                        decision = json.loads(json_str)
                        
                        # Validate decision format
                        decision = self._validate_decision_format(decision)
                        
                        # Perform compliance audit
                        compliance_audit = self.compliance_auditor.audit_decision(
                            decision, portfolio, prices, agent_reasoning=parsed_output
                        )
                        
                        # Log audit results
                        agent_logger.info("=== Compliance Audit ===")
                        agent_logger.info(compliance_audit.format_for_output())
                        
                        # Add audit to decision output
                        decision["compliance_audit"] = compliance_audit.to_dict()
                        decision["agent_reasoning"] = parsed_output["reasoning"]
                        
                        # Check if decision passed compliance
                        if compliance_audit.final_status == "FAIL":
                            logger.warning("Decision FAILED compliance audit - reverting to HOLD")
                            agent_logger.warning(f"COMPLIANCE FAILURE: {len(compliance_audit.violations)} critical violations")
                            
                            # Override to HOLD
                            decision = self._create_hold_decision(
                                f"Compliance failure: {len(compliance_audit.violations)} rule violations detected"
                            )
                            decision["compliance_audit"] = compliance_audit.to_dict()
                        
                        break
                        
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.error(f"Failed to parse final JSON: {e}")
                        agent_logger.error(f"JSON parse error: {e}")
                        continue
                
                # Handle tool calls
                if tool_calls:
                    tool_results = []
                    for tc in tool_calls:
                        func_name = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            args = {}
                        
                        logger.info(f"Calling tool: {func_name} with args: {args}")
                        
                        # Execute tool
                        result = self.tools.execute(func_name, args)
                        
                        # Log result
                        result_preview = str(result)[:200] if result else "None"
                        llm_logger.info(f"Tool {func_name} result: {result_preview}...")
                        
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result, ensure_ascii=False) if result else "null"
                        })
                        
                        # Notify step callback
                        if on_step:
                            on_step({
                                "role": "tool",
                                "name": func_name,
                                "content": result,
                                "metadata": {"tool_call_id": tc.id}
                            })
                    
                    messages.extend(tool_results)
                
                # If no tool calls and no decision, this might be intermediate reasoning
                if not tool_calls and "<FINAL_JSON>" not in msg_content:
                    logger.debug("Agent provided reasoning without tool calls or decision")
            
            # If loop finished without decision, return HOLD
            if decision is None:
                logger.warning("Agent did not provide decision within max steps - defaulting to HOLD")
                decision = self._create_hold_decision("No decision made within step limit (compliance-safe default)")
        
        except Exception as e:
            logger.error(f"Error in rule-aware agent execution: {e}", exc_info=True)
            decision = self._create_hold_decision(f"Error: {str(e)}")
        
        # Log final decision
        agent_logger.info("=== Final Decision ===")
        agent_logger.info(json.dumps(decision, ensure_ascii=False, indent=2))
        
        # Store in memory if enabled
        if self.memory and self.user_id and decision:
            try:
                memory_text = f"Decided to {decision['operation']} {decision.get('symbol', 'N/A')} at {current_time}. Reason: {decision.get('reason', 'N/A')}"
                self.memory.add(memory_text, user_id=self.user_id)
            except Exception as e:
                logger.error(f"Failed to store memory: {e}")
        
        return decision
    
    def _validate_decision_format(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and normalize decision format"""
        required_fields = ["operation", "symbol", "direction", "target_portion_of_balance", "leverage", "reason"]
        
        for field in required_fields:
            if field not in decision:
                if field == "direction":
                    decision[field] = "long"
                elif field == "target_portion_of_balance":
                    decision[field] = 0.0
                elif field == "leverage":
                    decision[field] = 1
                elif field == "reason":
                    decision[field] = "No reason provided"
                else:
                    decision[field] = "hold" if field == "operation" else "BTC"
        
        # Normalize values
        decision["operation"] = decision["operation"].lower()
        decision["direction"] = decision["direction"].lower()
        decision["symbol"] = decision["symbol"].upper()
        
        return decision
    
    def _create_hold_decision(self, reason: str) -> Dict[str, Any]:
        """Create a safe HOLD decision"""
        return {
            "operation": "hold",
            "symbol": "BTC",
            "direction": "long",
            "target_portion_of_balance": 0.0,
            "leverage": 1,
            "reason": reason
        }
