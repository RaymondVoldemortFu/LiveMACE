"""
Rule-Aware Agent - Trading agent with built-in rule compliance
"""
import os
import re
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Callable, Optional

from benchmark.builtin.prompts import get_prompt_resolver, require_profile_contract
from benchmark.prompts import PromptResolver
from ..base import BaseAgent
from ..llm_client import LLMClient
from ..tools import ToolRegistry
from config.agent_config import AgentConfig
from database.connection import get_db
from database.models import Account

from .rule_engine import RuleEngine
from .rule_validator import RuleValidator
from .compliance_auditor import ComplianceAuditor
from .llm_auditor import LLMAuditor

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
        agent_name: str = None,
        memory_enabled: bool = False,
        prompt_resolver: Optional[PromptResolver] = None,
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
            prompt_resolver: Optional profile-capable Prompt resolver
        """
        super().__init__(llm, tools, agent_name=agent_name)
        self.max_steps = max_steps
        self.user_id = user_id
        self.account_id = account_id
        self.prompt_resolver = get_prompt_resolver(prompt_resolver)
        require_profile_contract(
            self.prompt_resolver,
            "core.rule-aware.default",
            "rule_aware",
        )
        # self.memory = get_memory_service()  # Disabled: internal memory bypasses memory_enabled flag
        
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
                self.llm_auditor = LLMAuditor(
                    audit_llm,
                    prompt_resolver=self.prompt_resolver,
                )
                logger.info(f"LLM-based audit scoring enabled - Model: {audit_model}, Base URL: {audit_base_url or 'OpenAI Official'}")
        else:
            self.llm_auditor = None
        
        # Log loaded rules
        rule_summary = self.rule_engine.get_rule_summary()
        logger.info(f"Rule-Aware Agent initialized with rules: {rule_summary}")

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
        if squashed in {"<TRADE_DONE>", "TRADE_DONE>", "<TRADE_DONE", "TRADE_DONE"}:
            return True
        if "TRADE_DONE" in squashed and len(squashed) <= 32:
            return True
        return False

    @staticmethod
    def _has_filled_trade(item: Dict[str, Any]) -> bool:
        """Return whether a nested execute_trade record represents a fill."""

        if not isinstance(item, dict):
            return False
        result = item.get("result")
        args = item.get("args")
        if not isinstance(result, dict):
            return False
        if not result.get("executed") or result.get("error") is not None:
            return False
        operation = str(
            result.get("operation")
            or (args.get("operation") if isinstance(args, dict) else "")
            or ""
        ).strip().lower()
        if operation == "hold":
            return False
        if operation == "close_all" and not (result.get("closed_orders") or []):
            return False
        return bool(operation)
    
    def run(
        self, 
        portfolio: Dict[str, Any], 
        prices: Dict[str, float], 
        on_step: Optional[Callable[[Dict], None]] = None,
        trace_id: Optional[str] = None,
        decision_round_id: Optional[str] = None,
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
        system_prompt = self.prompt_resolver.render_slot(
            "core.rule-aware.default",
            "system",
            {
                "rule_documents": rule_documents,
                "current_time": current_time,
                "portfolio": json.dumps(portfolio, ensure_ascii=False, indent=2),
                "prices": json.dumps(prices, ensure_ascii=False, indent=2),
            },
        ).content
        
        # Initialize conversation
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "Begin your analysis. Use tools to gather information, check all applicable rules, and provide a compliant trading decision."
            }
        ]
        
        decision = None
        accumulated_content = ""  # Track accumulated assistant content across steps
        executed_trades: List[Dict] = []   # Track every execute_trade tool call
        trade_done_detected = False        # True when agent outputs <TRADE_DONE>

        try:
            for step in range(self.max_steps):
                remaining_steps = self.max_steps - step

                # Add reminder when running low on steps
                if remaining_steps <= AgentConfig.STEP_REMINDER_THRESHOLD and remaining_steps > 1:
                    reminder = self.prompt_resolver.render_slot(
                        "core.rule-aware.default",
                        "reminder",
                        {"remaining_steps": remaining_steps},
                    ).content
                    messages.append({"role": "user", "content": reminder})

                # Call LLM
                logger.info(f"Rule-Aware Agent step {step + 1}/{self.max_steps}")
                resp = self.llm.call(messages, tools=self.tools.openai_tools)

                msg_content = resp.content or ""
                tool_calls, tool_guard_warnings = LLMClient.apply_tool_call_guardrails(
                    getattr(resp, "tool_calls", None),
                    model=getattr(self.llm, "model", None),
                )
                if tool_guard_warnings:
                    logger.warning("Tool-call guardrails triggered: %s", " | ".join(tool_guard_warnings))
                    agent_logger.warning("Tool-call guardrails triggered: %s", " | ".join(tool_guard_warnings))

                # Log to llm_trace
                llm_logger.info(f"Step {step + 1} - Assistant response: {msg_content[:500]}...")

                # Add assistant message - use build_message_dict to preserve provider-specific
                # extra fields (e.g. Gemini's thought_signature on tool_calls)
                resp_dict = self.llm.build_assistant_message_dict(resp)
                if tool_calls:
                    resp_dict["tool_calls"] = tool_calls
                else:
                    resp_dict.pop("tool_calls", None)
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
                if self._is_trade_done_message(full_content):
                    logger.info("TRADE_DONE signal detected — ending multi-trade session")
                    trade_done_detected = True
                    break

                # ── Handle tool calls ──
                if tool_calls:
                    tool_results = []
                    for tc in tool_calls:
                        tc_id, func_name, tc_arguments = LLMClient.tool_call_parts(tc)
                        try:
                            args = json.loads(tc_arguments)
                        except json.JSONDecodeError:
                            args = {}

                        logger.info(f"Calling tool: {func_name} with args: {args}")
                        agent_logger.info(f"Executing tool '{func_name}' with args: {json.dumps(args, ensure_ascii=False)}")

                        # Execute tool - get tool from registry and call it
                        try:
                            result = self._invoke_llm_tool(
                                func_name,
                                args,
                                tool_call_id=tc_id,
                                decision_round_id=decision_round_id,
                            )
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
                            "tool_call_id": tc_id,
                            "content": json.dumps(result, ensure_ascii=False) if result else "null"
                        })

                        # Notify step callback
                        if on_step:
                            on_step({
                                "role": "tool",
                                "name": func_name,
                                "content": result,
                                "metadata": {"tool_call_id": tc_id}
                            })

                    messages.extend(tool_results)
                    if tool_guard_warnings:
                        warn_msg = LLMClient.tool_guardrail_warning_user_message(tool_guard_warnings)
                        messages.append(warn_msg)
                        if on_step:
                            on_step(dict(warn_msg))
                    if tool_results and self._requires_post_tool_user_message():
                        messages.append(LLMClient.gemini_post_tool_user_message())
                    # Reset accumulated content after tool calls
                    accumulated_content = ""

                # If no tool calls and no decision/signal, this is intermediate reasoning
                if not tool_calls and not self._is_trade_done_message(full_content):
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
                decision = self._create_hold_decision(
                    "No decision made within step limit (compliance-safe default)",
                    termination_reason="max_steps",
                )
                decision["protocol"] = "tool"
                decision["executed_trades"] = executed_trades
        
        except Exception as e:
            logger.error(f"Error in rule-aware agent execution: {e}", exc_info=True)
            decision = self._create_hold_decision(
                f"Error: {str(e)}",
                termination_reason="llm_error",
            )
            decision["protocol"] = "tool"
            decision["executed_trades"] = executed_trades
        
        # Log final decision
        agent_logger.info("=== Final Decision ===")
        agent_logger.info(json.dumps(decision, ensure_ascii=False, indent=2))
        
        return decision

    def _requires_post_tool_user_message(self) -> bool:
        requirement = getattr(
            self.llm,
            "requires_post_tool_user_message",
            None,
        )
        if callable(requirement):
            return bool(requirement())
        legacy_gemini_check = getattr(self.llm, "is_gemini_model", None)
        return bool(legacy_gemini_check()) if callable(legacy_gemini_check) else False

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
        Used by tool-mode (TRADE_DONE) decisions.
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

        # Perform LLM-based audit if enabled (skipped when AUDIT_OFFLINE=True)
        audit_offline = os.getenv("AUDIT_OFFLINE", "false").strip().lower() in ("true", "1", "yes")
        if self.enable_llm_audit and self.llm_auditor and not audit_offline:
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
            if self._has_filled_trade(t):
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
            "termination_reason": "trade_done" if last_trade else "hold",
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
    
    def _create_hold_decision(
        self,
        reason: str,
        *,
        termination_reason: str = "hold",
    ) -> Dict[str, Any]:
        """Create a safe HOLD decision"""
        return {
            "operation": "hold",
            "symbol": "BTC",
            "direction": "long",
            "target_portion_of_balance": 0.0,
            "leverage": 1,
            "reason": reason,
            "termination_reason": termination_reason,
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
