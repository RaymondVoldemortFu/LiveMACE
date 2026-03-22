"""
Rule-Aware Agent - Trading agent with built-in rule compliance
"""
import os
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
from database.connection import get_db
from database.models import Account

from .rule_engine import RuleEngine
from .rule_validator import RuleValidator
from .compliance_auditor import ComplianceAuditor
from .llm_auditor import LLMAuditor
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
        user_id: str = None,
        enable_llm_audit: bool = False,
        account_id: int = None,
        agent_name: str = None
    ):
        """
        Initialize Rule-Aware Agent

        Args:
            llm: LLM client
            tools: Tool registry
            rule_engine: Rule engine with loaded rules
            max_steps: Maximum reasoning steps
            user_id: User ID for memory
            enable_llm_audit: Whether to enable LLM-based audit scoring
            account_id: Account ID for updating audit statistics
            agent_name: Agent display name for logging
        """
        super().__init__(llm, tools, agent_name=agent_name)
        self.max_steps = max_steps
        self.user_id = user_id
        self.account_id = account_id
        self.memory = get_memory_service()
        
        # Rule compliance components
        self.rule_engine = rule_engine
        self.rule_validator = RuleValidator(rule_engine)
        self.compliance_auditor = ComplianceAuditor(rule_engine, self.rule_validator)
        
        # LLM-based audit (optional)
        self.enable_llm_audit = enable_llm_audit
        if enable_llm_audit:
            # Create separate LLM client for auditing using environment variables
            audit_api_key = os.getenv("AUDIT_API_KEY")
            audit_base_url = os.getenv("AUDIT_BASE_URL", None)
            audit_model = os.getenv("AUDIT_MODEL", "gpt-4o-mini")
            
            if not audit_api_key:
                logger.warning("AUDIT_API_KEY not set in environment, LLM audit will be disabled")
                self.llm_auditor = None
                self.enable_llm_audit = False
            else:
                audit_llm = LLMClient(
                    model=audit_model,
                    api_key=audit_api_key,
                    base_url=audit_base_url
                )
                self.llm_auditor = LLMAuditor(audit_llm)
                logger.info(f"LLM-based audit scoring enabled - Model: {audit_model}, Base URL: {audit_base_url or 'OpenAI Official'}")
        else:
            self.llm_auditor = None
        
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
                retrieved_memories = self.memory.search(query, account_id=self.user_id)
                
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
        accumulated_content = ""  # Track accumulated assistant content across steps
        executed_trades: List[Dict] = []   # Track every execute_trade tool call
        trade_done_detected = False        # True when agent outputs <TRADE_DONE>

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

                # Add assistant message - use build_message_dict to preserve provider-specific
                # extra fields (e.g. Gemini's thought_signature on tool_calls)
                resp_dict = LLMClient.build_message_dict(resp)
                messages.append(resp_dict)

                # Accumulate assistant content (handle multi-turn responses)
                if msg_content and not tool_calls:
                    accumulated_content += msg_content
                else:
                    # Reset accumulation if there are tool calls or empty content
                    accumulated_content = msg_content

                # Notify step callback
                if on_step:
                    on_step({
                        "role": "assistant",
                        "content": msg_content,
                        "tool_calls": tool_calls,
                        "metadata": {"step": step + 1}
                    })

                full_content = accumulated_content if accumulated_content else msg_content

                # ── NEW PROTOCOL: agent uses execute_trade tool then outputs <TRADE_DONE> ──
                if "<TRADE_DONE>" in full_content:
                    logger.info("TRADE_DONE signal detected — ending multi-trade session")
                    trade_done_detected = True
                    break

                # ── LEGACY PROTOCOL: agent outputs <FINAL_JSON>...</FINAL_JSON> ──
                if "<FINAL_JSON>" in full_content and "</FINAL_JSON>" in full_content:
                    logger.info("Final decision detected in agent output (legacy FINAL_JSON protocol)")

                    # Parse the full output for compliance audit
                    parsed_output = self.compliance_auditor.parse_agent_output(full_content)

                    # Extract JSON decision
                    try:
                        start = full_content.index("<FINAL_JSON>") + len("<FINAL_JSON>")
                        end = full_content.index("</FINAL_JSON>")
                        json_str = full_content[start:end].strip()
                        logger.info(f"Extracted JSON string ({len(json_str)} chars): {json_str[:200]}...")

                        decision = json.loads(json_str)
                        logger.info(f"Parsed decision: operation={decision.get('operation')}, symbol={decision.get('symbol')}, direction={decision.get('direction')}")

                        # Validate decision format
                        decision = self._validate_decision_format(decision)
                        logger.info(f"Decision after validation: {decision}")

                        decision = self._attach_compliance_audit(decision, portfolio, prices, full_content, parsed_output)

                        break

                    except (json.JSONDecodeError, ValueError) as e:
                        logger.error(f"Failed to parse final JSON: {e}")
                        agent_logger.error(f"JSON parse error: {e}")
                        continue

                # ── Handle tool calls ──
                if tool_calls:
                    tool_results = []
                    for tc in tool_calls:
                        func_name = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            args = {}

                        logger.info(f"Calling tool: {func_name} with args: {args}")
                        agent_logger.info(f"Executing tool '{func_name}' with args: {json.dumps(args, ensure_ascii=False)}")

                        # Execute tool - get tool from registry and call it
                        try:
                            tool = self.tools.get(func_name)
                            result = tool(**args)
                        except Exception as tool_err:
                            logger.error(f"Tool execution failed for {func_name}: {tool_err}", exc_info=True)
                            result = {"error": f"Tool execution failed: {str(tool_err)}"}

                        # Track execute_trade calls for tool-mode compliance audit
                        if func_name == "execute_trade" and isinstance(result, dict):
                            executed_trades.append({"args": args, "result": result})
                            logger.info(f"execute_trade recorded: op={args.get('operation')} sym={args.get('symbol')} executed={result.get('executed')}")

                        # Log result
                        result_preview = str(result)[:200] if result else "None"
                        llm_logger.info(f"Tool {func_name} result: {result_preview}...")
                        agent_logger.info(f"Tool '{func_name}' result: {json.dumps(result, ensure_ascii=False)}")

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
                    # Reset accumulated content after tool calls
                    accumulated_content = ""

                # If no tool calls and no decision/signal, this is intermediate reasoning
                if not tool_calls and "<FINAL_JSON>" not in full_content and "<TRADE_DONE>" not in full_content:
                    logger.debug(f"Agent provided reasoning without tool calls or decision (accumulated: {len(accumulated_content)} chars)")
                    # Don't reset accumulated_content here - it will be used in next iteration

            # ── Post-loop: build decision for tool-mode (TRADE_DONE) sessions ──
            if trade_done_detected and decision is None:
                decision = self._build_tool_mode_decision(
                    executed_trades, accumulated_content, portfolio, prices
                )

            # ── Fallback: no decision produced at all ──
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
                self.memory.add(memory_text, account_id=self.user_id)
            except Exception as e:
                logger.error(f"Failed to store memory: {e}")
        
        return decision
    
    def _attach_compliance_audit(
        self,
        decision: Dict[str, Any],
        portfolio: Dict[str, Any],
        prices: Dict[str, float],
        full_content: str,
        parsed_output: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Run the compliance audit against *decision* and attach the results.
        Used by both FINAL_JSON (legacy) and tool-mode (TRADE_DONE) paths.
        """
        if parsed_output is None:
            parsed_output = self.compliance_auditor.parse_agent_output(full_content)

        # Perform compliance audit
        logger.info("Starting compliance audit...")
        logger.info(f"Portfolio state: total_assets={portfolio.get('total_assets')}")
        logger.info(f"Number of rules to check: {len(self.rule_engine.get_all_rules())}")

        compliance_audit = self.compliance_auditor.audit_decision(
            decision, portfolio, prices, agent_reasoning=parsed_output
        )

        logger.info(f"Compliance audit completed: status={compliance_audit.final_status}, violations={len(compliance_audit.violations)}")
        for v in compliance_audit.violations:
            logger.info(f"  Violation: [{v.severity}] {v.rule.id} - {v.message}")

        agent_logger.info("=== Compliance Audit ===")
        agent_logger.info(compliance_audit.format_for_output())

        decision["compliance_audit"] = compliance_audit.to_dict()
        decision["agent_reasoning"] = parsed_output.get("reasoning", "")
        logger.info("Compliance audit attached to decision")

        if compliance_audit.final_status == "FAIL":
            logger.warning(f"Decision has compliance violations: {len(compliance_audit.violations)} critical violations")
            agent_logger.warning(f"COMPLIANCE WARNING: {len(compliance_audit.violations)} critical violations")

        # Perform LLM-based audit if enabled
        if self.enable_llm_audit and self.llm_auditor:
            try:
                logger.info("Performing LLM-based audit scoring...")
                rule_documents = self.rule_engine.format_rules_for_prompt()
                market_state = {"portfolio": portfolio, "prices": prices}

                llm_audit_result = self.llm_auditor.audit_agent_reasoning(
                    rules=rule_documents,
                    market_state=market_state,
                    agent_output=full_content,
                )

                logger.info(f"LLM audit: coverage={llm_audit_result.get('coverage')}, final_score={llm_audit_result.get('final_normalized_score')}")
                decision["llm_audit"] = llm_audit_result

                audit_report = self.llm_auditor.format_audit_report(llm_audit_result)
                agent_logger.info("=== LLM Audit Report ===")
                agent_logger.info(audit_report)

            except Exception as e:
                logger.error(f"LLM audit failed: {e}", exc_info=True)
                decision["llm_audit"] = {"error": str(e), "final_normalized_score": 0.0}

        return decision

    def _build_tool_mode_decision(
        self,
        executed_trades: List[Dict],
        agent_content: str,
        portfolio: Dict[str, Any],
        prices: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        Build a summary decision dict after a tool-mode (TRADE_DONE) session.

        Uses the last successfully executed trade as the representative action.
        If no trades were executed the session is treated as a hold.

        The returned dict contains:
          - protocol="tool"  (signals trading_commands to skip duplicate execution)
          - executed_trades  (full list of execute_trade call records)
          - compliance_audit (post-session programmatic rule check)
        """
        last_trade: Optional[Dict] = None
        for t in reversed(executed_trades):
            if isinstance(t.get("result"), dict) and t["result"].get("executed"):
                last_trade = t
                break

        if last_trade:
            args = last_trade["args"]
            op = str(args.get("operation", "open")).lower()
            sym = str(args.get("symbol", "BTC")).upper()
            direction = str(args.get("direction", "long")).lower()
            portion = float(args.get("target_portion_of_balance") or 0.0)
            leverage = int(args.get("leverage") or 1)
            reason = str(args.get("reason") or f"Tool-mode: executed {op} {sym}")
        else:
            op = "hold"
            sym = "BTC"
            direction = "long"
            portion = 0.0
            leverage = 1
            reason = "No trades executed during session (TRADE_DONE without execute_trade)"

        summary = {
            "protocol": "tool",
            "operation": op,
            "symbol": sym,
            "direction": direction,
            "target_portion_of_balance": portion,
            "leverage": leverage,
            "reason": reason,
            "executed_trades": executed_trades,
        }

        logger.info(f"Tool-mode session summary: {len(executed_trades)} trade call(s), representative={op} {sym}")

        # Run compliance audit using original portfolio snapshot.
        # Rules that query the DB directly (R2-01, R2-03, R2-06) will reflect the
        # post-trade state because execute_trade_tool already committed each trade.
        # Portfolio-level fields (cash, total_assets) use the pre-session snapshot
        # which is an acceptable approximation for the scoring record.
        try:
            summary = self._attach_compliance_audit(summary, portfolio, prices, agent_content)
        except Exception as e:
            logger.error(f"Compliance audit failed for tool-mode session: {e}", exc_info=True)

        return summary

    def _validate_decision_format(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and normalize decision format"""
        required_fields = ["operation", "symbol", "direction", "target_portion_of_balance", "leverage", "reason"]
        
        # Set defaults for missing or None fields
        for field in required_fields:
            if field not in decision or decision[field] is None:
                if field == "direction":
                    decision[field] = "long"
                elif field == "target_portion_of_balance":
                    decision[field] = 0.0
                elif field == "leverage":
                    decision[field] = 1
                elif field == "reason":
                    decision[field] = "No reason provided"
                elif field == "operation":
                    decision[field] = "hold"
                elif field == "symbol":
                    decision[field] = "BTC"
        
        # Normalize string values (handle None gracefully)
        if decision["operation"]:
            decision["operation"] = str(decision["operation"]).lower()
        else:
            decision["operation"] = "hold"
            
        if decision["direction"]:
            decision["direction"] = str(decision["direction"]).lower()
        else:
            decision["direction"] = "long"
            
        if decision["symbol"]:
            decision["symbol"] = str(decision["symbol"]).upper()
        else:
            decision["symbol"] = "BTC"
        
        logger.info(f"Validated decision: {decision['operation']} {decision['symbol']} {decision['direction']} portion={decision['target_portion_of_balance']} leverage={decision['leverage']}")
        
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
    
    def _update_account_audit_stats(self, audit_result: Dict[str, Any]) -> None:
        """
        Update account's cumulative audit statistics
        
        Args:
            audit_result: LLM audit result with scores
        """
        try:
            # Skip if audit failed
            if "error" in audit_result:
                logger.warning(f"Skipping audit stats update due to audit error: {audit_result.get('error')}")
                return
            
            # Extract scores
            final_score = audit_result.get("final_normalized_score", 0.0)
            coverage_score = audit_result.get("coverage", {}).get("score", 0)
            conflict_score = audit_result.get("conflict", {}).get("score", 0)
            
            # Update database
            db = next(get_db())
            try:
                account = db.query(Account).filter(Account.id == self.account_id).first()
                if not account:
                    logger.error(f"Account {self.account_id} not found, cannot update audit stats")
                    return
                
                # Get current values
                count = account.llm_audit_count or 0
                
                # Calculate new averages using incremental formula:
                # new_avg = (old_avg * count + new_value) / (count + 1)
                if count == 0:
                    # First audit
                    account.llm_audit_avg_score = final_score
                    account.llm_audit_avg_coverage = float(coverage_score)
                    account.llm_audit_avg_conflict = float(conflict_score)
                else:
                    # Incremental update
                    old_avg_score = account.llm_audit_avg_score or 0.0
                    old_avg_coverage = account.llm_audit_avg_coverage or 0.0
                    old_avg_conflict = account.llm_audit_avg_conflict or 0.0
                    
                    account.llm_audit_avg_score = (old_avg_score * count + final_score) / (count + 1)
                    account.llm_audit_avg_coverage = (old_avg_coverage * count + coverage_score) / (count + 1)
                    account.llm_audit_avg_conflict = (old_avg_conflict * count + conflict_score) / (count + 1)
                
                # Increment count
                account.llm_audit_count = count + 1
                
                # Commit changes
                db.commit()
                
                logger.info(f"Updated account {self.account_id} audit stats - "
                          f"Count: {account.llm_audit_count}, "
                          f"Avg Score: {account.llm_audit_avg_score:.3f}, "
                          f"Avg Coverage: {account.llm_audit_avg_coverage:.2f}, "
                          f"Avg Conflict: {account.llm_audit_avg_conflict:.2f}")
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Failed to update account audit stats: {e}", exc_info=True)

