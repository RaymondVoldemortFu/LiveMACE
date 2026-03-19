import json
import logging
import os
import socket
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .base import BaseAgent
from .llm_client import LLMClient
from .memory import get_memory_service
from .prompts.advanced_multi_agent_prompts import (
    ANALYST_AGENT_PROMPT,
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
                "Review technical setup, exposure, and actionable levels for BTC/ETH/SOL. "
                f"Objective: {objective}"
            )
        if agent_name == "NewsAgent":
            return (
                "Collect recent market-moving events and sentiment for BTC/ETH/SOL and explain directional impact. "
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
        self, final_decision: Dict[str, Any], decision: Dict[str, Any], allow_partial: bool = False
    ) -> Tuple[bool, str]:
        if not isinstance(final_decision, dict) or not final_decision:
            return False, "Missing final_decision object."

        missing_core = []
        if self._agent_call_count("TradingAgent") == 0:
            missing_core.append("TradingAgent")
        if self._agent_call_count("NewsAgent") == 0:
            missing_core.append("NewsAgent")
        if missing_core:
            return False, f"Finish blocked: missing core evidence from {', '.join(missing_core)}."

        leverage = final_decision.get("leverage", 1)
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

        formatted_prompt = system_prompt.format(
            instruction=instruction,
            portfolio=json.dumps(portfolio, ensure_ascii=False),
            prices=json.dumps(prices, ensure_ascii=False),
        )

        messages = [{"role": "system", "content": formatted_prompt}]

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

        current_response = ""
        for _ in range(sub_agent_steps):
            resp = self.llm.call(messages, tools=agent_tools if agent_tools else None)

            msg_content = resp.content or ""
            tool_calls = resp.tool_calls

            if hasattr(resp, "model_dump"):
                resp_dict = resp.model_dump()
            else:
                resp_dict = resp.dict()
            messages.append(resp_dict)

            if on_step:
                on_step(
                    {
                        "role": "assistant",
                        "content": f"[{agent_name}] {msg_content}" if msg_content else None,
                        "tool_calls": [
                            t.model_dump() if hasattr(t, "model_dump") else t for t in tool_calls
                        ]
                        if tool_calls
                        else None,
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
            else:
                current_response = msg_content
                break

        if not current_response:
            # Force a final summary response without tools if the agent only used tools.
            fallback_prompt = (
                f"{formatted_prompt}\n\n"
                "Your previous responses relied only on tool calls or were empty. "
                "Return ONLY JSON now with a concise summary and actionable details. Do not call tools."
            )
            resp = self.llm.call([{"role": "system", "content": fallback_prompt}])
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
                memories = self.memory.search(query, user_id=self.user_id)
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

        for step in range(self.max_steps):
            context_str = "\n".join(self.context[-16:]) if self.context else "No prior actions."

            formatted_manager_prompt = Advanced_MANAGER_PROMPT.format(
                objective=objective,
                context=context_str,
                portfolio=json.dumps(portfolio, ensure_ascii=False),
                prices=json.dumps(prices, ensure_ascii=False),
                evidence_book=self._format_evidence_book(),
                conflicts=self._format_conflicts(),
                collaboration_state=self._format_collaboration_state(step),
            )

            resp = self.llm.call([{"role": "system", "content": formatted_manager_prompt}])
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
                candidate_final = decision.get("final_decision")
                allow_partial = step >= max(0, self.max_steps - 3)
                if allow_partial:
                    basis = decision.setdefault("decision_basis", {}) or {}
                    if not basis.get("supporting_evidence_ids") and self.evidence_log:
                        basis["supporting_evidence_ids"] = [ev["id"] for ev in self.evidence_log[-2:]]
                ok, msg = self._validate_finish(candidate_final, decision, allow_partial=allow_partial)
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

                final_decision = candidate_final
                final_decision["reason"] = f"[MultiAgent] {self._compose_final_reason(final_decision.get('reason', ''), decision)}"
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
            }

        if self.memory and self.user_id:
            try:
                session_summary = "\n".join(self.context)
                self.memory.add(
                    session_summary,
                    user_id=self.user_id,
                    metadata={"trace_id": trace_id} if trace_id else {},
                )
            except Exception as e:
                logger.error("Failed to save to memory: %s", e)

        return final_decision
