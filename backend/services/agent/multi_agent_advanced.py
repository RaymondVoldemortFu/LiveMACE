import json
import logging
import os
import re
import socket
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import BaseAgent
from .llm_client import LLMClient
from .memory import get_memory_service
from .prompts.advanced_multi_agent_prompts import (
    ANALYST_AGENT_PROMPT,
    ADVANCED_EXECUTION_PROMPT,
    CODER_AGENT_PROMPT,
    CRITIC_AGENT_PROMPT,
    NEWS_AGENT_PROMPT,
    TRADING_AGENT_PROMPT,
    Advanced_MANAGER_PROMPT,
)
from .tools import ToolRegistry

logger = logging.getLogger(__name__)


class AdvancedMultiAgent(BaseAgent):
    """Manager-driven multi-agent architecture for a single trading decision."""

    VALID_AGENTS = {"TradingAgent", "NewsAgent", "CoderAgent", "AnalystAgent", "CriticAgent"}
    TERMINATION_TOKEN = "<TRADE_DONE>"
    NEWS_AGENT_MAX_SEARCH_CALLS = 3

    def __init__(self, llm: LLMClient, tools: ToolRegistry, max_steps: int = 15, user_id: str = None):
        super().__init__(llm, tools)
        self.max_steps = max_steps
        self.user_id = user_id
        self.memory = get_memory_service()

        self.context: List[str] = []
        self.evidence_log: List[Dict[str, Any]] = []

    def _notify_evaluator(self, trace_id: Optional[str]) -> None:
        if not trace_id:
            return
        host = os.getenv("EVAL_NOTIFY_HOST", "127.0.0.1").strip() or "127.0.0.1"
        port_str = os.getenv("EVAL_NOTIFY_PORT", "").strip()
        base_str = os.getenv("EVAL_NOTIFY_PORT_BASE", "").strip()

        if port_str:
            try:
                port = int(port_str)
            except ValueError:
                logger.warning("Invalid EVAL_NOTIFY_PORT: %s", port_str)
                return
        else:
            if not base_str:
                port = 9010
            else:
                try:
                    port = int(base_str) + int(self.user_id)
                except (ValueError, TypeError):
                    logger.warning("Invalid EVAL_NOTIFY_PORT_BASE or user_id: %s", base_str)
                    return

        payload = {
            "trace_id": trace_id,
            "account_id": int(self.user_id) if str(self.user_id).isdigit() else self.user_id,
            "event": "finish",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(data, (host, port))
        except Exception as e:
            logger.warning("Failed to notify evaluator: %s", e)
        finally:
            sock.close()

    def _extract_json_dict(self, text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None

        candidates = [text.strip()]
        if "```json" in text:
            try:
                candidates.insert(0, text.split("```json", 1)[1].split("```", 1)[0].strip())
            except Exception:
                pass
        elif "```" in text:
            try:
                candidates.insert(0, text.split("```", 1)[1].split("```", 1)[0].strip())
            except Exception:
                pass

        for candidate in candidates:
            if not candidate:
                continue
            try:
                obj = json.loads(candidate)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass

        start = text.find("{")
        while start != -1:
            depth = 0
            for idx in range(start, len(text)):
                ch = text[idx]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        snippet = text[start : idx + 1]
                        try:
                            obj = json.loads(snippet)
                            if isinstance(obj, dict):
                                return obj
                        except Exception:
                            break
            start = text.find("{", start + 1)

        return None

    def _safe_list(self, value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if value is None:
            return []
        return [value]

    def _stringify(self, value: Any, max_len: int = 320) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=False)
        text = " ".join(text.split())
        if len(text) > max_len:
            return text[: max_len - 3] + "..."
        return text

    @staticmethod
    def _safe_split_once(text: str, marker: str) -> Tuple[str, str]:
        if marker in text:
            left, right = text.split(marker, 1)
            return left.strip(), right.strip()
        return text.strip(), ""

    def _build_manager_messages(
        self,
        objective: str,
        context_str: str,
        portfolio: Dict[str, Any],
        prices: Dict[str, Any],
        step: int,
    ) -> List[Dict[str, str]]:
        """System: policy/schema. User: current trading task state."""
        intro, _ = self._safe_split_once(Advanced_MANAGER_PROMPT, "Trading objective:")
        _, protocol_and_schema = self._safe_split_once(Advanced_MANAGER_PROMPT, "Decision Protocol:")

        system_parts = [intro]
        if protocol_and_schema:
            system_parts.append(f"Decision Protocol:\n{protocol_and_schema}")
        system_prompt = "\n\n".join([p for p in system_parts if p]).strip()

        user_prompt = (
            "Current trading task state:\n"
            f"Trading objective:\n{objective}\n\n"
            f"Portfolio:\n{json.dumps(portfolio, ensure_ascii=False)}\n\n"
            f"Market Prices:\n{json.dumps(prices, ensure_ascii=False)}\n\n"
            "Tradable Universe (strict):\n"
            "- Crypto: BTC, ETH, SOL, BNB, XRP, DOGE\n"
            "- US Stocks: AAPL, NVDA, GOOGL, META, AMZN, TSLA, PG, JNJ, UNH, JPM, V, BA, XOM, NEE, AMT, PLD, LIN\n\n"
            f"Evidence Book (use evidence IDs when citing prior findings):\n{self._format_evidence_book()}\n\n"
            f"Current Context:\n{context_str}\n\n"
            f"Known Conflicts/Tensions:\n{self._format_conflicts()}\n\n"
            f"Collaboration State:\n{self._format_collaboration_state(step)}\n\n"
            "Decide the next action now and return ONLY JSON."
        )
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    def _build_sub_agent_messages(
        self,
        agent_name: str,
        prompt_template: str,
        instruction: str,
        portfolio: Dict[str, Any],
        prices: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """System: role/spec. User: concrete instruction + runtime context."""
        context_marker = "Instruction:" if "Instruction:" in prompt_template else "Context:"
        intro, _ = self._safe_split_once(prompt_template, context_marker)
        _, schema_tail = self._safe_split_once(prompt_template, "Return ONLY JSON:")

        system_parts = [intro]
        if schema_tail:
            system_parts.append(f"Return ONLY JSON:\n{schema_tail}")
        system_prompt = "\n\n".join([p for p in system_parts if p]).strip()

        user_lines = [f"Current task for {agent_name}:", instruction]
        if agent_name in {"TradingAgent", "AnalystAgent", "CriticAgent"}:
            user_lines.extend(
                [
                    "",
                    f"Portfolio:\n{json.dumps(portfolio, ensure_ascii=False)}",
                    "",
                    f"Prices:\n{json.dumps(prices, ensure_ascii=False)}",
                ]
            )
        user_lines.append("")
        user_lines.append("Respond now.")
        user_prompt = "\n".join(user_lines)

        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    def _derive_stance(self, text: str, sentiment: str = "") -> str:
        source = f"{text} {sentiment}".lower()
        bullish_keys = ["bullish", "upside", "recovery", "long", "buy"]
        bearish_keys = ["bearish", "downside", "sell", "short", "risk-off", "crash"]

        bullish = any(k in source for k in bullish_keys)
        bearish = any(k in source for k in bearish_keys)

        if bullish and bearish:
            return "mixed"
        if bullish:
            return "bullish"
        if bearish:
            return "bearish"
        return "neutral"

    def _normalize_sub_agent_output(self, agent_name: str, raw_text: str) -> Dict[str, Any]:
        parsed = self._extract_json_dict(raw_text)

        summary = ""
        recommendation_text = ""
        risks: List[str] = []
        sentiment = ""

        if parsed:
            summary = self._stringify(
                parsed.get("summary")
                or parsed.get("recommended_resolution")
                or parsed.get("final_warning")
                or parsed
            )

            recommendation = parsed.get("recommendation") or parsed.get("recommendation_impact")
            if recommendation:
                recommendation_text = self._stringify(recommendation)

            risks.extend([self._stringify(r) for r in self._safe_list(parsed.get("risks")) if self._stringify(r)])
            risks.extend([self._stringify(r) for r in self._safe_list(parsed.get("hidden_risks")) if self._stringify(r)])
            risks.extend([self._stringify(r) for r in self._safe_list(parsed.get("downside_scenarios")) if self._stringify(r)])
            risks.extend([self._stringify(r) for r in self._safe_list(parsed.get("risk_controls")) if self._stringify(r)])
            risks.extend([self._stringify(r) for r in self._safe_list(parsed.get("veto_conditions")) if self._stringify(r)])
            sentiment = self._stringify(parsed.get("sentiment"))
        else:
            summary = self._stringify(raw_text, max_len=600)

        stance = self._derive_stance(f"{summary} {recommendation_text}", sentiment)

        evidence_id = f"E{len(self.evidence_log) + 1}"
        return {
            "id": evidence_id,
            "agent": agent_name,
            "summary": summary or "No summary provided",
            "recommendation": recommendation_text,
            "risks": risks[:4],
            "stance": stance,
            "raw": self._stringify(raw_text, max_len=3000),
        }

    def _build_objective(self, portfolio: Dict[str, Any]) -> str:
        positions = portfolio.get("positions") or {}
        if positions:
            return "Manage existing exposure and update positioning only when evidence is strong and risk is controlled."
        return "Seek a high-conviction setup with controlled downside and disciplined position sizing."

    def _format_evidence_book(self, limit: int = 10) -> str:
        if not self.evidence_log:
            return "No evidence recorded yet."

        items = self.evidence_log[-limit:]
        lines = []
        for ev in items:
            rec = ev.get("recommendation") or "no explicit recommendation"
            risks = "; ".join(ev.get("risks") or []) or "no explicit risk flags"
            lines.append(
                f"- {ev['id']} | {ev['agent']} | stance={ev['stance']} | summary={ev['summary']} | "
                f"recommendation={rec} | risks={risks}"
            )
        return "\n".join(lines)

    def _detect_conflicts(self) -> List[str]:
        if len(self.evidence_log) < 2:
            return []

        recent = self.evidence_log[-8:]
        stances = {ev["agent"]: ev.get("stance", "neutral") for ev in recent}

        conflicts = []
        if "TradingAgent" in stances and "NewsAgent" in stances:
            t_stance = stances["TradingAgent"]
            n_stance = stances["NewsAgent"]
            if {t_stance, n_stance} == {"bullish", "bearish"}:
                conflicts.append("TradingAgent and NewsAgent imply opposing directional bias.")

        for ev in recent:
            text = f"{ev.get('summary', '')} {ev.get('recommendation', '')}".lower()
            if any(k in text for k in ["veto", "do not", "avoid", "overexposed", "liquidity risk"]):
                conflicts.append(f"{ev['id']} raises a caution that may conflict with aggressive positioning.")

        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for c in conflicts:
            if c not in seen:
                seen.add(c)
                deduped.append(c)
        return deduped

    def _format_conflicts(self) -> str:
        conflicts = self._detect_conflicts()
        if not conflicts:
            return "No explicit unresolved conflict at the moment."
        return "\n".join([f"- {c}" for c in conflicts])

    def _agent_call_count(self, agent_name: str) -> int:
        return sum(1 for ev in self.evidence_log if ev.get("agent") == agent_name)

    def _format_collaboration_state(self, step: int) -> str:
        parts = [
            f"step={step + 1}/{self.max_steps}",
            f"TradingAgent_calls={self._agent_call_count('TradingAgent')}",
            f"NewsAgent_calls={self._agent_call_count('NewsAgent')}",
            f"CoderAgent_calls={self._agent_call_count('CoderAgent')}",
            f"AnalystAgent_calls={self._agent_call_count('AnalystAgent')}",
            f"CriticAgent_calls={self._agent_call_count('CriticAgent')}",
        ]
        return ", ".join(parts)

    def _default_instruction(self, agent_name: str, objective: str) -> str:
        if agent_name == "TradingAgent":
            return (
                "Review technical setup, exposure, and actionable levels across the tradable universe. "
                f"Objective: {objective}"
            )
        if agent_name == "NewsAgent":
            return (
                "Collect recent market-moving events and sentiment across the tradable universe and explain directional impact. "
                f"Objective: {objective}"
            )
        if agent_name == "CoderAgent":
            return (
                "Run a focused calculation to validate momentum/volatility assumptions and summarize the impact on risk sizing. "
                f"Objective: {objective}"
            )
        if agent_name == "AnalystAgent":
            return "Synthesize available evidence, enumerate conflicts, and propose a clear resolution path."
        if agent_name == "CriticAgent":
            return "Challenge the current thesis, list downside paths, and provide risk controls before execution."
        return "Provide relevant analysis for the current trading objective."

    def _fallback_agent(self) -> Optional[str]:
        if self._agent_call_count("TradingAgent") == 0:
            return "TradingAgent"
        if self._agent_call_count("NewsAgent") == 0:
            return "NewsAgent"
        if self._detect_conflicts() and self._agent_call_count("AnalystAgent") == 0:
            return "AnalystAgent"
        if self._agent_call_count("CriticAgent") == 0 and len(self.evidence_log) >= 2:
            return "CriticAgent"
        return None

    def _append_skip_calls(self, skip_calls: Any) -> None:
        for item in self._safe_list(skip_calls):
            if not isinstance(item, dict):
                continue
            agent = item.get("agent")
            reason = item.get("reason")
            if agent and reason:
                self.context.append(f"Manager explicitly skipped {agent}. Reason: {reason}")

    def _validate_finish(
        self, execution_plan: Any, decision: Dict[str, Any], allow_partial: bool = False
    ) -> Tuple[bool, str]:
        plan_items = self._safe_list(execution_plan)
        if not plan_items and "execution_plan" not in decision:
            return False, "Missing execution_plan object."

        missing_core = []
        if self._agent_call_count("TradingAgent") == 0:
            missing_core.append("TradingAgent")
        if self._agent_call_count("NewsAgent") == 0:
            missing_core.append("NewsAgent")
        if missing_core:
            return False, f"Finish blocked: missing core evidence from {', '.join(missing_core)}."

        leverage = 1
        leverages = []
        for item in plan_items:
            if isinstance(item, dict):
                leverages.append(item.get("leverage", 1))
        if leverages:
            leverage = max(leverages)
        try:
            leverage = int(leverage)
        except Exception:
            leverage = 1
        if leverage > 3 and self._agent_call_count("CriticAgent") == 0 and not allow_partial:
            return False, "Finish blocked: leveraged action requires critical risk review."

        basis = decision.get("decision_basis") or {}
        if not basis.get("supporting_evidence_ids"):
            if allow_partial and self.evidence_log:
                basis["supporting_evidence_ids"] = [ev["id"] for ev in self.evidence_log[-2:]]
                decision["decision_basis"] = basis
            elif not allow_partial:
                return False, "Finish blocked: decision_basis.supporting_evidence_ids is empty."

        return True, ""

    def _compose_final_reason(self, base_reason: str, decision: Dict[str, Any]) -> str:
        basis = decision.get("decision_basis") or {}
        support = self._safe_list(basis.get("supporting_evidence_ids") or decision.get("evidence_ids"))
        oppose = self._safe_list(basis.get("opposing_evidence_ids"))
        controls = self._safe_list(basis.get("risk_controls"))
        why_not = self._stringify(basis.get("why_not_alternative"))
        tradeoff = self._stringify(decision.get("collaboration_tradeoff"))

        parts = [self._stringify(base_reason, max_len=800)]
        if support:
            parts.append(f"support={', '.join([str(x) for x in support])}")
        if oppose:
            parts.append(f"opposition={', '.join([str(x) for x in oppose])}")
        if controls:
            parts.append(f"risk_controls={', '.join([str(x) for x in controls])}")
        if why_not:
            parts.append(f"why_not_alternative={why_not}")
        if tradeoff:
            parts.append(f"collaboration_tradeoff={tradeoff}")

        return " | ".join([p for p in parts if p])

    def _expected_execution_calls(self, execution_plan: List[Dict[str, Any]]) -> int:
        expected = 0
        for step in execution_plan:
            if not isinstance(step, dict):
                continue
            op = str(step.get("operation") or "").strip().lower()
            if not op:
                continue
            # Keep hold as an executable step as well for tool-mode trace consistency.
            expected += 1
        return expected

    def _is_done_message(self, text: str) -> bool:
        if not text:
            return False
        normalized = text.strip().replace("`", "")
        if normalized == self.TERMINATION_TOKEN:
            return True
        if re.search(r"<\s*TRADE_DONE\s*>", normalized, re.IGNORECASE):
            return True
        squashed = re.sub(r"\s+", "", normalized).upper()
        # Be tolerant to near-miss variants seen in production traces.
        if squashed in {"<TRADE_DONE>", "TRADE_DONE>", "<TRADE_DONE", "TRADE_DONE"}:
            return True
        if "TRADE_DONE" in squashed and len(squashed) <= 32:
            return True
        return False

    def _validate_news_query(self, args: Dict[str, Any]) -> Optional[str]:
        query = str(args.get("query") or "")
        if not query.strip():
            return "News query is empty. Provide a focused, recent-market query."

        years = re.findall(r"\b(20\d{2})\b", query)
        if not years:
            return None

        current_year = datetime.now(timezone.utc).year
        stale_years = []
        for y in years:
            try:
                year_num = int(y)
            except ValueError:
                continue
            if year_num < current_year - 1:
                stale_years.append(year_num)
        if stale_years:
            return (
                f"Query targets stale year(s): {sorted(set(stale_years))}. "
                "Focus on current/recent catalysts unless explicitly asked for historical backtest."
            )
        return None

    def _build_execution_messages(
        self,
        execution_plan: List[Dict[str, Any]],
        decision: Dict[str, Any],
        portfolio: Dict[str, Any],
        prices: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        expected_calls = self._expected_execution_calls(execution_plan)
        execution_context = {
            "execution_plan": execution_plan,
            "expected_execute_trade_calls": expected_calls,
            "decision_basis": decision.get("decision_basis") or {},
            "manager_reason": decision.get("reason") or "",
            "collaboration_tradeoff": decision.get("collaboration_tradeoff") or "",
            "evidence_ids": self._safe_list(decision.get("evidence_ids")),
        }
        user_prompt = (
            "Execute the approved plan now.\n\n"
            f"Execution Context:\n{json.dumps(execution_context, ensure_ascii=False)}\n\n"
            f"Portfolio:\n{json.dumps(portfolio, ensure_ascii=False)}\n\n"
            f"Market Prices:\n{json.dumps(prices, ensure_ascii=False)}\n\n"
            "Execute the execution_plan in order. "
            f"Expected execute_trade calls for this plan: {expected_calls}. "
            "Call execute_trade one or more times as needed. "
            f"When complete, output exactly: {self.TERMINATION_TOKEN}"
        )
        return [{"role": "system", "content": ADVANCED_EXECUTION_PROMPT}, {"role": "user", "content": user_prompt}]

    def _run_execution_stage(
        self,
        execution_plan: List[Dict[str, Any]],
        decision: Dict[str, Any],
        portfolio: Dict[str, Any],
        prices: Dict[str, Any],
        on_step: Optional[Callable] = None,
    ) -> List[Dict[str, Any]]:
        messages = self._build_execution_messages(execution_plan, decision, portfolio, prices)
        available_tools_names = [t["function"]["name"] for t in self.tools.openai_tools]
        allowed_tools = ["execute_trade"]
        valid_tools = [t for t in allowed_tools if t in available_tools_names]
        stage_tools = [t for t in self.tools.openai_tools if t["function"]["name"] in valid_tools]
        executed_trades: List[Dict[str, Any]] = []
        expected_calls = self._expected_execution_calls(execution_plan)

        for _ in range(24):
            resp = self.llm.call(messages, tools=stage_tools if stage_tools else None)
            msg_content = resp.content or ""
            tool_calls = resp.tool_calls

            resp_dict = self.llm.build_assistant_message_dict(resp)
            messages.append(resp_dict)

            if on_step:
                on_step(
                    {
                        "role": "assistant",
                        "content": f"[Execution] {msg_content}" if msg_content else None,
                        "tool_calls": LLMClient.tool_calls_to_roundtrip_dicts(tool_calls),
                        "metadata": {"agent": "ExecutionAgent"},
                    }
                )

            if tool_calls:
                for tc in tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except Exception as e:
                        args = {}
                        result = {"error": f"Invalid tool arguments for {name}: {e}"}
                    else:
                        tool_func = self.tools.get(name)
                        if tool_func is None:
                            result = {"error": f"Tool not found: {name}"}
                        else:
                            try:
                                result = tool_func(**args)
                            except Exception as e:
                                result = {"error": f"Tool execution failed for {name}: {e}"}

                    if name == "execute_trade":
                        executed_trades.append(result if isinstance(result, dict) else {"raw_result": str(result)})

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                    messages.append(tool_msg)

                    if on_step:
                        on_step(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "name": name,
                                "content": json.dumps(result, ensure_ascii=False),
                                "metadata": {"agent": "ExecutionAgent"},
                            }
                        )
                if expected_calls > 0 and len(executed_trades) < expected_calls:
                    remaining = expected_calls - len(executed_trades)
                    next_step = execution_plan[len(executed_trades)] if len(executed_trades) < len(execution_plan) else {}
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"Continue execution. Remaining execute_trade calls required: {remaining}. "
                                f"Next step should follow this plan item: {json.dumps(next_step, ensure_ascii=False)}. "
                                f"After all required calls, output ONLY: {self.TERMINATION_TOKEN}"
                            ),
                        }
                    )
                elif self.llm.is_gemini_model():
                    messages.append(LLMClient.gemini_post_tool_user_message())
                continue

            if self._is_done_message(msg_content):
                if expected_calls == 0 or len(executed_trades) >= expected_calls:
                    return executed_trades
                remaining = expected_calls - len(executed_trades)
                next_step = execution_plan[len(executed_trades)] if len(executed_trades) < len(execution_plan) else {}
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Termination denied: only {len(executed_trades)}/{expected_calls} execute_trade calls completed. "
                            f"Execute remaining {remaining} step(s). Next required step: {json.dumps(next_step, ensure_ascii=False)}. "
                            f"When fully complete, output ONLY: {self.TERMINATION_TOKEN}"
                        ),
                    }
                )
                continue

            reminder = (
                "Continue execution using execute_trade if needed. "
                f"When complete, output ONLY: {self.TERMINATION_TOKEN}"
            )
            messages.append({"role": "user", "content": reminder})

        if expected_calls > 0 and len(executed_trades) < expected_calls:
            logger.warning(
                "Execution stage exited by step limit before completing plan: completed=%s expected=%s",
                len(executed_trades),
                expected_calls,
            )
        return executed_trades

    def _run_sub_agent(
        self,
        agent_name: str,
        instruction: str,
        portfolio: Dict[str, Any],
        prices: Dict[str, Any],
        on_step: Optional[Callable] = None,
    ) -> str:
        if agent_name == "TradingAgent":
            system_prompt = TRADING_AGENT_PROMPT
            allowed_tools = ["get_market_snapshot", "get_kline_history", "get_account_state"]
        elif agent_name == "NewsAgent":
            system_prompt = NEWS_AGENT_PROMPT
            allowed_tools = ["consult_search_agent"]
        elif agent_name == "CoderAgent":
            system_prompt = CODER_AGENT_PROMPT
            allowed_tools = ["run_python_script", "read_file", "write_file", "execute_shell_command"]
        elif agent_name == "AnalystAgent":
            system_prompt = ANALYST_AGENT_PROMPT
            allowed_tools = []
        elif agent_name == "CriticAgent":
            system_prompt = CRITIC_AGENT_PROMPT
            allowed_tools = []
        else:
            return f"Error: Unknown agent {agent_name}"

        available_tools_names = [t["function"]["name"] for t in self.tools.openai_tools]
        valid_tools = [t for t in allowed_tools if t in available_tools_names]

        messages = self._build_sub_agent_messages(
            agent_name=agent_name,
            prompt_template=system_prompt,
            instruction=instruction,
            portfolio=portfolio,
            prices=prices,
        )

        if on_step:
            on_step(
                {
                    "role": "system",
                    "content": f"Manager delegated to {agent_name}: {instruction}",
                    "metadata": {"agent": "Manager"},
                }
            )

        sub_agent_steps = 20
        agent_tools = [t for t in self.tools.openai_tools if t["function"]["name"] in valid_tools]
        news_search_calls = 0

        current_response = ""
        for _ in range(sub_agent_steps):
            current_tools = agent_tools
            if agent_name == "NewsAgent" and news_search_calls >= self.NEWS_AGENT_MAX_SEARCH_CALLS:
                current_tools = []
            resp = self.llm.call(messages, tools=current_tools if current_tools else None)

            msg_content = resp.content or ""
            tool_calls = resp.tool_calls

            resp_dict = self.llm.build_assistant_message_dict(resp)
            messages.append(resp_dict)

            if on_step:
                on_step(
                    {
                        "role": "assistant",
                        "content": f"[{agent_name}] {msg_content}" if msg_content else None,
                        "tool_calls": LLMClient.tool_calls_to_roundtrip_dicts(tool_calls),
                        "metadata": {"agent": agent_name},
                    }
                )

            if tool_calls:
                for tc in tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except Exception as e:
                        args = {}
                        result = {"error": f"Invalid tool arguments for {name}: {e}"}
                    else:
                        tool_func = self.tools.get(name)
                        if tool_func is None:
                            result = {"error": f"Tool not found: {name}"}
                        else:
                            if agent_name == "NewsAgent" and name == "consult_search_agent":
                                if news_search_calls >= self.NEWS_AGENT_MAX_SEARCH_CALLS:
                                    result = {
                                        "error": (
                                            f"NewsAgent search budget reached ({self.NEWS_AGENT_MAX_SEARCH_CALLS}). "
                                            "Use gathered evidence and return final JSON without more searches."
                                        )
                                    }
                                else:
                                    query_issue = self._validate_news_query(args)
                                    if query_issue:
                                        result = {"error": query_issue}
                                    else:
                                        try:
                                            result = tool_func(**args)
                                            news_search_calls += 1
                                        except Exception as e:
                                            result = {"error": f"Tool execution failed for {name}: {e}"}
                            else:
                                try:
                                    result = tool_func(**args)
                                except Exception as e:
                                    result = {"error": f"Tool execution failed for {name}: {e}"}

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                    messages.append(tool_msg)

                    if on_step:
                        on_step(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "name": name,
                                "content": json.dumps(result, ensure_ascii=False),
                                "metadata": {"agent": agent_name},
                            }
                        )
                if agent_name == "NewsAgent" and news_search_calls >= self.NEWS_AGENT_MAX_SEARCH_CALLS:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Search budget reached. Do not call more tools. "
                                "Return ONLY JSON now using already collected evidence."
                            ),
                        }
                    )
                elif self.llm.is_gemini_model():
                    messages.append(LLMClient.gemini_post_tool_user_message())
            else:
                current_response = msg_content
                break

        if not current_response:
            # Force a final summary response without tools if the agent only used tools.
            fallback_user_prompt = (
                f"Current task for {agent_name}:\n{instruction}\n\n"
                "Your previous responses relied only on tool calls or were empty. "
                "Return ONLY JSON now with a concise summary and actionable details. Do not call tools."
            )
            fallback_messages = list(messages[:2])
            fallback_messages[1] = {"role": "user", "content": fallback_user_prompt}
            resp = self.llm.call(fallback_messages)
            current_response = resp.content or ""

        return current_response

    def run(
        self,
        portfolio: Dict[str, Any],
        prices: Dict[str, float],
        on_step: Optional[Callable[[Dict], None]] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.info("Starting Advanced Multi-Agent decision process")

        memory_content = ""
        if self.memory and self.user_id:
            try:
                query = f"Trading context: {len(portfolio.get('positions', {}))} positions. Market: {list(prices.keys())}"
                memories = self.memory.search(query, account_id=self.user_id)
                if memories:
                    texts = [m.get("memory") or m.get("text") or m.get("content") for m in memories]
                    memory_content = "\n".join([f"- {t}" for t in texts if t])
                    if on_step and memory_content:
                        on_step(
                            {
                                "role": "memory",
                                "content": f"Retrieved Memories:\n{memory_content}",
                                "metadata": {"type": "memory"},
                            }
                        )
            except Exception as e:
                logger.error("Memory retrieval failed: %s", e)

        self.context = []
        self.evidence_log = []

        if memory_content:
            self.context.append(f"Relevant Memories:\n{memory_content}")

        objective = self._build_objective(portfolio)
        final_decision = None
        executed_trades: List[Dict[str, Any]] = []

        for step in range(self.max_steps):
            context_str = "\n".join(self.context[-16:]) if self.context else "No prior actions."

            manager_messages = self._build_manager_messages(
                objective=objective,
                context_str=context_str,
                portfolio=portfolio,
                prices=prices,
                step=step,
            )
            resp = self.llm.call(manager_messages)
            content = resp.content or ""

            if on_step:
                on_step(
                    {
                        "role": "assistant",
                        "content": f"[Manager] {content}",
                        "metadata": {"agent": "Manager"},
                    }
                )

            decision = self._extract_json_dict(content)
            if not decision:
                self.context.append(f"Step {step + 1}: Manager output invalid JSON. Content: {self._stringify(content, 800)}")
                continue

            action = decision.get("next_action")
            reason = self._stringify(decision.get("reason"))
            tradeoff = self._stringify(decision.get("collaboration_tradeoff"))
            evidence_ids = self._safe_list(decision.get("evidence_ids"))

            if reason:
                self.context.append(f"Step {step + 1}: Manager rationale: {reason}")
            if tradeoff:
                self.context.append(f"Step {step + 1}: Collaboration tradeoff: {tradeoff}")
            if evidence_ids:
                self.context.append(
                    f"Step {step + 1}: Referenced evidence IDs: {', '.join([str(x) for x in evidence_ids])}"
                )

            self._append_skip_calls(decision.get("skip_calls"))

            if action == "call_agent":
                agent_name = decision.get("agent_name")
                if agent_name not in self.VALID_AGENTS:
                    agent_name = self._fallback_agent() or "AnalystAgent"

                instruction = decision.get("instruction") or self._default_instruction(agent_name, objective)
                self.context.append(
                    f"Step {step + 1}: Manager decided to call {agent_name}. Reason: {reason or 'No explicit reason.'}"
                )

                full_instruction = instruction
                if agent_name in {"AnalystAgent", "CriticAgent"}:
                    full_instruction = (
                        f"{instruction}\n\n"
                        f"Evidence book:\n{self._format_evidence_book(limit=6)}\n\n"
                        f"Conflicts:\n{self._format_conflicts()}"
                    )

                result = self._run_sub_agent(agent_name, full_instruction, portfolio, prices, on_step)
                evidence = self._normalize_sub_agent_output(agent_name, result)
                self.evidence_log.append(evidence)

                self.context.append(
                    f"Step {step + 1}: Recorded {evidence['id']} from {agent_name}. "
                    f"Summary: {evidence['summary']} | stance={evidence['stance']}"
                )

                if on_step:
                    on_step(
                        {
                            "role": "assistant",
                            "content": (
                                f"[Manager] Evidence {evidence['id']} accepted from {agent_name}. "
                                f"stance={evidence['stance']}"
                            ),
                            "metadata": {"agent": "Manager"},
                        }
                    )

            elif action == "finish":
                candidate_plan = decision.get("execution_plan")
                allow_partial = step >= max(0, self.max_steps - 3)
                if allow_partial:
                    basis = decision.setdefault("decision_basis", {}) or {}
                    if not basis.get("supporting_evidence_ids") and self.evidence_log:
                        basis["supporting_evidence_ids"] = [ev["id"] for ev in self.evidence_log[-2:]]
                ok, msg = self._validate_finish(candidate_plan, decision, allow_partial=allow_partial)
                if not ok:
                    self.context.append(f"Step {step + 1}: {msg}")
                    if on_step:
                        on_step(
                            {
                                "role": "assistant",
                                "content": f"[Manager] {msg}",
                                "metadata": {"agent": "Manager"},
                            }
                        )
                    continue

                execution_plan = []
                for item in self._safe_list(candidate_plan):
                    if isinstance(item, dict):
                        execution_plan.append(item)
                if not execution_plan and decision.get("final_decision"):
                    execution_plan = [decision.get("final_decision")]

                executed_trades = self._run_execution_stage(
                    execution_plan=execution_plan,
                    decision=decision,
                    portfolio=portfolio,
                    prices=prices,
                    on_step=on_step,
                )

                final_decision = {
                    "operation": "hold",
                    "symbol": "",
                    "direction": "long",
                    "target_portion_of_balance": 0.0,
                    "leverage": 1,
                    "reason": f"[MultiAgent] {self._compose_final_reason(decision.get('execution_summary', ''), decision)}",
                    "protocol": "tool",
                    "executed_trades": executed_trades,
                    "execution_plan": execution_plan,
                    "decision_basis": decision.get("decision_basis") or {},
                }
                self._notify_evaluator(trace_id)
                break

            else:
                self.context.append(f"Step {step + 1}: Manager returned unknown action '{action}'.")

        if not final_decision:
            final_decision = {
                "operation": "hold",
                "symbol": "",
                "direction": "long",
                "target_portion_of_balance": 0.0,
                "leverage": 1,
                "reason": "MultiAgent Manager did not reach a conclusion within max steps.",
                "protocol": "tool",
                "executed_trades": executed_trades,
            }

        if self.memory and self.user_id:
            try:
                session_summary = "\n".join(self.context)
                self.memory.add(
                    session_summary,
                    account_id=self.user_id,
                    metadata={"trace_id": trace_id} if trace_id else {},
                )
            except Exception as e:
                logger.error("Failed to save to memory: %s", e)

        return final_decision
