import json
import logging
import os
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from benchmark.builtin.prompts import (
    get_builtin_prompt_registry,
    get_prompt_resolver,
    require_profile_contract,
)
from services.time_source import now_in_tz

from .base import BaseAgent
from .llm_client import LLMClient
from .tools import ToolRegistry

logger = logging.getLogger(__name__)
llm_logger = logging.getLogger("llm_trace")

CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"}
US_SYMBOLS = {"AAPL", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "PG", "JNJ", "UNH", "JPM", "V", "BA", "XOM", "NEE", "AMT", "PLD", "LIN"}


@dataclass(frozen=True)
class _ExecutionStageResult:
    trades: List[Dict[str, Any]]
    matched_plan_items: int
    expected_plan_items: int

    @property
    def complete(self) -> bool:
        return self.matched_plan_items == self.expected_plan_items


class AdvancedMultiAgent(BaseAgent):
    """Manager-driven multi-agent architecture for a single trading decision."""

    VALID_AGENTS = {"TradingAgent", "NewsAgent", "CoderAgent", "AnalystAgent", "CriticAgent"}
    TERMINATION_TOKEN = "<TRADE_DONE>"
    NEWS_AGENT_MAX_SEARCH_CALLS = 3
    EXECUTION_MAX_STEPS = 24
    PROMPT_PROFILE_ID = "core.advanced-multi-agent.default"

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
            "advanced_multi_agent",
        )

        self.context: List[str] = []
        self.evidence_log: List[Dict[str, Any]] = []

    def _agent_label(self) -> str:
        return self.agent_name or self.__class__.__name__

    @staticmethod
    def _has_filled_trade(executed_trades: List[Dict[str, Any]]) -> bool:
        """Return whether execution actually opened or closed a position."""

        for item in executed_trades:
            if not isinstance(item, dict) or item.get("executed") is not True:
                continue
            operation = str(item.get("operation") or "").strip()
            if operation == "hold":
                continue
            if operation == "close_all":
                closed = item.get("closed_orders") or []
                if isinstance(closed, (list, tuple)) and any(
                    isinstance(order, dict) for order in closed
                ):
                    return True
                continue
            if operation in {"open", "close", "all_in"}:
                return True
        return False

    def _log_llm_trace(
        self,
        *,
        current_sub_agent: str,
        phase: str,
        payload: Dict[str, Any],
    ) -> None:
        entry = {
            "agent_name": self._agent_label(),
            "current_sub_agent": current_sub_agent,
            "phase": phase,
            "payload": payload,
        }
        try:
            llm_logger.info(json.dumps(entry, ensure_ascii=False))
        except Exception:
            llm_logger.info(
                "advanced_multi_agent_llm_trace agent=%s sub_agent=%s phase=%s",
                self._agent_label(),
                current_sub_agent,
                phase,
            )

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

    def _current_time_context(self) -> str:
        tz_utc_8 = timezone(timedelta(hours=8))
        current_time_utc_8 = now_in_tz(tz_utc_8).strftime("%Y-%m-%d %H:%M:%S")
        current_time_utc = now_in_tz(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return f"Current Time (UTC+8): {current_time_utc_8}\nCurrent Time (UTC): {current_time_utc}"

    def _tradable_universe_context(self) -> str:
        return (
            "Tradable Universe (strict):\n"
            "- Crypto: BTC, ETH, SOL, BNB, XRP, DOGE\n"
            "- US Stocks: AAPL, NVDA, GOOGL, META, AMZN, TSLA, PG, JNJ, UNH, JPM, V, BA, XOM, NEE, AMT, PLD, LIN"
        )

    def _infer_market_from_symbol(self, symbol: Any) -> Optional[str]:
        symbol_text = str(symbol or "").strip().upper()
        if symbol_text in CRYPTO_SYMBOLS:
            return "CRYPTO"
        if symbol_text in US_SYMBOLS:
            return "US"
        return None

    def _normalize_execution_plan(self, execution_plan: Any) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in self._safe_list(execution_plan):
            if not isinstance(item, dict):
                continue
            normalized_item = dict(item)
            operation = str(normalized_item.get("operation") or "").strip().lower()
            if operation:
                normalized_item["operation"] = operation

            size_mode = str(normalized_item.get("size_mode") or "").strip().lower()
            if size_mode == "close_ratio":
                normalized_item["size_mode"] = "portion"
                if normalized_item.get("close_ratio") in (None, ""):
                    fallback_ratio = normalized_item.get("target_portion_of_balance")
                    if fallback_ratio not in (None, ""):
                        normalized_item["close_ratio"] = fallback_ratio
            elif size_mode:
                normalized_item["size_mode"] = size_mode

            if operation == "all_in":
                normalized_item["size_mode"] = "all_in"
            elif operation == "close_all":
                normalized_item["size_mode"] = "close_all"

            market = str(normalized_item.get("market") or "").strip().upper()
            inferred_market = self._infer_market_from_symbol(normalized_item.get("symbol"))
            if market in {"CRYPTO", "US"}:
                normalized_item["market"] = market
            elif inferred_market:
                normalized_item["market"] = inferred_market
            normalized.append(normalized_item)
        return normalized

    def _recommendation_signature(self, candidate: Any) -> str:
        if not isinstance(candidate, dict):
            return self._stringify(candidate, max_len=200)

        market = candidate.get("market") or self._infer_market_from_symbol(candidate.get("symbol"))
        key = {
            "priority": candidate.get("priority"),
            "operation": candidate.get("operation"),
            "symbol": candidate.get("symbol"),
            "market": market,
            "direction": candidate.get("direction"),
            "target_portion_of_balance": candidate.get("target_portion_of_balance"),
            "usd_amount": candidate.get("usd_amount"),
            "close_ratio": candidate.get("close_ratio"),
            "leverage": candidate.get("leverage"),
        }
        return json.dumps(key, ensure_ascii=False, sort_keys=True)

    def _collect_recommendations(self, parsed: Dict[str, Any]) -> List[Any]:
        collected: List[Any] = []
        primary_recommendations = self._safe_list(parsed.get("recommendations"))
        legacy_primary = parsed.get("recommendation")
        legacy_candidates = self._safe_list(parsed.get("trade_candidates") or parsed.get("top_opportunities"))

        if primary_recommendations:
            collected.extend(primary_recommendations)
        elif legacy_primary:
            collected.extend(self._safe_list(legacy_primary))

        collected.extend(legacy_candidates)

        deduped: List[Any] = []
        seen = set()
        for candidate in collected:
            signature = self._recommendation_signature(candidate)
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(candidate)
        return deduped

    def _summarize_recommendations(self, candidates: Any, limit: int = 4) -> List[str]:
        summaries: List[str] = []
        for candidate in self._safe_list(candidates)[:limit]:
            if not isinstance(candidate, dict):
                continue
            priority = candidate.get("priority")
            operation = self._stringify(candidate.get("operation"))
            symbol = self._stringify(candidate.get("symbol"))
            market = self._stringify(candidate.get("market") or self._infer_market_from_symbol(candidate.get("symbol")))
            direction = self._stringify(candidate.get("direction"))
            leverage = candidate.get("leverage")
            target_portion = candidate.get("target_portion_of_balance")
            close_ratio = candidate.get("close_ratio")
            usd_amount = candidate.get("usd_amount")
            rationale = self._stringify(candidate.get("rationale") or candidate.get("reason"), max_len=120)

            if not symbol:
                continue

            parts = []
            if priority not in (None, ""):
                parts.append(f"P{priority}")
            parts.extend([operation or "hold", symbol])
            if market:
                parts[-1] = f"{symbol}/{market}"
            if direction:
                parts.append(direction)
            if leverage not in (None, ""):
                parts.append(f"lev={leverage}")
            if target_portion not in (None, ""):
                parts.append(f"portion={target_portion}")
            if close_ratio not in (None, ""):
                parts.append(f"close_ratio={close_ratio}")
            if usd_amount not in (None, ""):
                parts.append(f"usd={usd_amount}")
            if rationale:
                parts.append(rationale)
            summaries.append(" ".join([p for p in parts if p]))
        return summaries

    def _prepare_news_search_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        prepared = dict(args)
        query = str(prepared.get("query") or "")
        query_lower = query.lower()
        historical_pattern = re.compile(
            r"\b(history|historical|backtest|archive|previous cycle|last cycle|prior cycle)\b|\b20\d{2}\b"
        )
        looks_historical = bool(historical_pattern.search(query_lower))

        topic = str(prepared.get("topic") or "").strip().lower()
        if not topic or topic == "general":
            prepared["topic"] = "news"

        time_range = str(prepared.get("time_range") or "").strip().lower()
        if (not time_range or time_range == "none") and not looks_historical:
            prepared["time_range"] = "week"

        if "max_results" not in prepared:
            prepared["max_results"] = 5
        return prepared

    def _build_manager_messages(
        self,
        objective: str,
        context_str: str,
        portfolio: Dict[str, Any],
        prices: Dict[str, Any],
        step: int,
    ) -> List[Dict[str, str]]:
        """System: policy/schema. User: current trading task state."""
        variables = {
            "objective": objective,
            "portfolio": json.dumps(portfolio, ensure_ascii=False),
            "prices": json.dumps(prices, ensure_ascii=False),
            "evidence_book": self._format_evidence_book(),
            "context": context_str,
            "conflicts": self._format_conflicts(),
            "collaboration_state": self._format_collaboration_state(step),
        }
        rendered_prompt = self.prompt_resolver.render_slot(
            self.PROMPT_PROFILE_ID,
            "manager",
            variables,
        ).content
        builtin_prompt = self._matches_builtin_prompt_slot(
            "manager",
            rendered_prompt,
            variables,
        )
        if builtin_prompt:
            intro, _ = self._safe_split_once(rendered_prompt, "Trading objective:")
            _, protocol_and_schema = self._safe_split_once(
                rendered_prompt, "Decision Protocol:"
            )
            system_parts = [intro]
            if protocol_and_schema:
                system_parts.append(f"Decision Protocol:\n{protocol_and_schema}")
            system_prompt = "\n\n".join(
                part for part in system_parts if part
            ).strip()
        else:
            # External/account Prompt output is authoritative and opaque. Its
            # declared variables are already rendered here, so neither parse
            # the content nor repeat those values in the user message.
            system_prompt = rendered_prompt.strip()

        builtin_user_prompt = (
            "Current trading task state:\n"
            f"Trading objective:\n{objective}\n\n"
            f"Portfolio:\n{json.dumps(portfolio, ensure_ascii=False)}\n\n"
            f"Market Prices:\n{json.dumps(prices, ensure_ascii=False)}\n\n"
            f"{self._current_time_context()}\n\n"
            f"{self._tradable_universe_context()}\n\n"
            f"Evidence Book (use evidence IDs when citing prior findings):\n{self._format_evidence_book()}\n\n"
            f"Current Context:\n{context_str}\n\n"
            f"Known Conflicts/Tensions:\n{self._format_conflicts()}\n\n"
            f"Collaboration State:\n{self._format_collaboration_state(step)}\n\n"
            "Decide the next action now and return ONLY JSON."
        )
        if builtin_prompt:
            user_prompt = builtin_user_prompt
        else:
            user_prompt = (
                "Supplemental runtime context for the externally supplied prompt:\n"
                f"{self._current_time_context()}\n\n"
                f"{self._tradable_universe_context()}\n\n"
                "Follow the complete system prompt and return its requested response now."
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
        slot = self._agent_prompt_slot(agent_name)
        variables = {"instruction": instruction}
        if slot in {"trading", "analyst", "critic"}:
            variables.update(
                portfolio=json.dumps(portfolio, ensure_ascii=False),
                prices=json.dumps(prices, ensure_ascii=False),
            )
        builtin_prompt = self._matches_builtin_prompt_slot(
            slot,
            prompt_template,
            variables,
        )
        if builtin_prompt:
            context_marker = "Instruction:" if "Instruction:" in prompt_template else "Context:"
            intro, _ = self._safe_split_once(prompt_template, context_marker)
            _, schema_tail = self._safe_split_once(prompt_template, "Return ONLY JSON:")
            system_parts = [intro]
            if schema_tail:
                system_parts.append(f"Return ONLY JSON:\n{schema_tail}")
            system_prompt = "\n\n".join(
                part for part in system_parts if part
            ).strip()
            user_lines = [
                f"Current task for {agent_name}:",
                instruction,
                "",
                self._current_time_context(),
            ]
        else:
            system_prompt = prompt_template.strip()
            user_lines = [
                "Supplemental runtime context for the externally supplied prompt:",
                self._current_time_context(),
            ]
        if agent_name in {"TradingAgent", "NewsAgent"}:
            user_lines.extend(["", self._tradable_universe_context()])
        if builtin_prompt and agent_name in {
            "TradingAgent",
            "AnalystAgent",
            "CriticAgent",
            "CoderAgent",
        }:
            user_lines.extend(
                [
                    "",
                    f"Portfolio:\n{json.dumps(portfolio, ensure_ascii=False)}",
                    "",
                    f"Prices:\n{json.dumps(prices, ensure_ascii=False)}",
                ]
            )
        if agent_name == "CoderAgent":
            user_lines.extend(
                [
                    "",
                    "Workspace guidance:",
                    "- Reuse exact file_path values returned by tools when available.",
                    "- Do not assume a specific workspace file exists unless its path was explicitly provided or confirmed.",
                    "- If exact file paths are unknown, inspect /workspace first before calling read_file.",
                    "- When using run_python_script, pass a JSON object with script_content and print() the result.",
                ]
            )
        user_lines.append("")
        user_lines.append("Respond now.")
        user_prompt = "\n".join(user_lines)

        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    def _matches_builtin_prompt_slot(
        self,
        slot: str,
        rendered_content: str,
        variables: Dict[str, Any],
    ) -> bool:
        """Identify unchanged built-in content without requiring provenance APIs."""

        builtin_content = get_builtin_prompt_registry().render_slot(
            self.PROMPT_PROFILE_ID,
            slot,
            variables,
        ).content
        return rendered_content == builtin_content

    @staticmethod
    def _agent_prompt_slot(agent_name: str) -> str:
        slots = {
            "TradingAgent": "trading",
            "NewsAgent": "news",
            "CoderAgent": "coder",
            "AnalystAgent": "analyst",
            "CriticAgent": "critic",
        }
        try:
            return slots[agent_name]
        except KeyError as exc:
            raise ValueError(f"Unknown agent: {agent_name}") from exc

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
        recommendation_summaries: List[str] = []

        if parsed:
            summary = self._stringify(
                parsed.get("summary")
                or parsed.get("recommended_resolution")
                or parsed.get("final_warning")
                or parsed
            )

            recommendation_impact = parsed.get("recommendation_impact")
            if recommendation_impact:
                recommendation_text = self._stringify(recommendation_impact)

            recommendation_summaries = self._summarize_recommendations(self._collect_recommendations(parsed))
            if recommendation_summaries:
                candidate_text = " ; ".join(recommendation_summaries)
                if recommendation_text:
                    recommendation_text = f"{recommendation_text} | recommendations={candidate_text}"
                else:
                    recommendation_text = candidate_text

            risks.extend([self._stringify(r) for r in self._safe_list(parsed.get("risks")) if self._stringify(r)])
            risks.extend([self._stringify(r) for r in self._safe_list(parsed.get("hidden_risks")) if self._stringify(r)])
            risks.extend([self._stringify(r) for r in self._safe_list(parsed.get("downside_scenarios")) if self._stringify(r)])
            risks.extend([self._stringify(r) for r in self._safe_list(parsed.get("risk_controls")) if self._stringify(r)])
            risks.extend([self._stringify(r) for r in self._safe_list(parsed.get("veto_conditions")) if self._stringify(r)])
            sentiment = self._stringify(parsed.get("sentiment"))
        else:
            summary = self._stringify(raw_text, max_len=600)

        if recommendation_summaries:
            summary = f"{summary} | ranked_recommendations={' ; '.join(recommendation_summaries)}"

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
            return (
                "Manage existing exposure, but do not tunnel on current holdings. "
                "Review open positions and continue scanning the rest of the tradable universe for stronger opportunities."
            )
        return "Seek a high-conviction setup with controlled downside and disciplined position sizing."

    def _format_evidence_book(self, limit: int = 10) -> str:
        if not self.evidence_log:
            return "No evidence recorded yet."

        items = self.evidence_log[-limit:]
        lines = []
        for ev in items:
            rec = ev.get("recommendation") or "no explicit actions"
            risks = "; ".join(ev.get("risks") or []) or "no explicit risk flags"
            lines.append(
                f"- {ev['id']} | {ev['agent']} | stance={ev['stance']} | summary={ev['summary']} | "
                f"actions={rec} | risks={risks}"
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
        if self._agent_call_count("TradingAgent") == 0:
            return False, "Finish blocked: TradingAgent must be called at least once before finishing."

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
        return len(self._executable_plan_items(execution_plan))

    @staticmethod
    def _executable_plan_items(
        execution_plan: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        valid_operations = {"open", "close", "hold", "all_in", "close_all"}
        return [
            step
            for step in execution_plan
            if isinstance(step, dict)
            and str(step.get("operation") or "").strip().lower()
            in valid_operations
        ]

    def _execution_call_matches_plan_item(
        self,
        plan_item: Dict[str, Any],
        arguments: Dict[str, Any],
    ) -> bool:
        expected = self._execution_command_signature(plan_item, plan_item=True)
        actual = self._execution_command_signature(arguments, plan_item=False)
        return expected is not None and actual is not None and actual == expected

    @staticmethod
    def _canonical_decimal(value: Any) -> Tuple[bool, Optional[str]]:
        if value is None or value == "":
            return True, None
        if isinstance(value, bool):
            return False, None
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return False, None
        if not number.is_finite():
            return False, None
        if number == 0:
            return True, "0"
        return True, format(number.normalize(), "f")

    @staticmethod
    def _canonical_leverage(value: Any) -> Optional[int]:
        if value is None or value == "":
            return 1
        if isinstance(value, bool):
            return 1
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            try:
                return int(value)
            except (OverflowError, ValueError):
                return None
        if isinstance(value, str):
            match = re.search(r"-?\d+", value.strip())
            if match:
                return int(match.group(0))
            return 1
        try:
            return int(value)
        except (TypeError, ValueError):
            return 1
        except OverflowError:
            return None

    def _execution_command_signature(
        self,
        values: Dict[str, Any],
        *,
        plan_item: bool,
    ) -> Optional[Dict[str, Any]]:
        """Normalize every model-controlled execute_trade argument for approval matching."""

        operation = str(values.get("operation") or "").strip().lower()
        if not operation:
            return None
        symbol = str(values.get("symbol") or "").strip().upper()

        raw_market = str(values.get("market") or "").strip().upper()
        market_aliases = {
            "STOCK": "US",
            "STOCKS": "US",
            "HYPERLIQUID": "CRYPTO",
        }
        raw_market = market_aliases.get(raw_market, raw_market)
        if plan_item and raw_market not in {"CRYPTO", "US"}:
            market = self._infer_market_from_symbol(symbol) or "CRYPTO"
        else:
            market = raw_market or "CRYPTO"

        direction = str(values.get("direction") or "long").strip().lower()
        size_mode = str(values.get("size_mode") or "portion").strip().lower()
        target_portion = values.get("target_portion_of_balance")
        close_ratio = values.get("close_ratio")
        if size_mode == "close_ratio":
            size_mode = "portion"
            if close_ratio is None or close_ratio == "":
                close_ratio = target_portion
        normalized_numbers: Dict[str, Optional[str]] = {}
        for field, value in (
            ("target_portion_of_balance", target_portion),
            ("usd_amount", values.get("usd_amount")),
            ("close_ratio", close_ratio),
        ):
            valid, normalized = self._canonical_decimal(value)
            if not valid:
                return None
            normalized_numbers[field] = normalized

        leverage = self._canonical_leverage(values.get("leverage"))
        if leverage is None:
            return None
        # Trading policy validates leverage bounds/US leverage before reducing
        # a valid hold command to its canonical 1x representation.
        if operation == "hold" and leverage <= 10 and not (
            market == "US" and leverage != 1
        ):
            leverage = 1

        sizing_mode: Optional[str] = size_mode
        sizing_value: Optional[str] = None
        if operation in {"hold", "close_all", "all_in"}:
            sizing_mode = None
        elif size_mode == "usd" and normalized_numbers["usd_amount"] is not None:
            sizing_value = normalized_numbers["usd_amount"]
        elif normalized_numbers["close_ratio"] is not None:
            sizing_mode = "close_ratio"
            sizing_value = normalized_numbers["close_ratio"]
        elif normalized_numbers["target_portion_of_balance"] is not None:
            sizing_value = normalized_numbers["target_portion_of_balance"]

        return {
            "operation": operation,
            "symbol": symbol,
            "market": market,
            "direction": direction,
            "sizing_mode": sizing_mode,
            "sizing_value": sizing_value,
            "leverage": leverage,
        }

    @staticmethod
    def _execution_result_completed_plan_item(result: Any) -> bool:
        return (
            isinstance(result, dict)
            and result.get("executed") is True
            and result.get("accepted") is not False
            and not result.get("error")
            and not result.get("reject_code")
        )

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
        system_prompt = self.prompt_resolver.render_slot(
            self.PROMPT_PROFILE_ID,
            "execution",
            {},
        ).content
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    def _run_execution_stage(
        self,
        execution_plan: List[Dict[str, Any]],
        decision: Dict[str, Any],
        portfolio: Dict[str, Any],
        prices: Dict[str, Any],
        on_step: Optional[Callable] = None,
        decision_round_id: Optional[str] = None,
    ) -> _ExecutionStageResult:
        messages = self._build_execution_messages(execution_plan, decision, portfolio, prices)
        available_tools_names = [t["function"]["name"] for t in self.tools.openai_tools]
        allowed_tools = ["execute_trade"]
        valid_tools = [t for t in allowed_tools if t in available_tools_names]
        stage_tools = [t for t in self.tools.openai_tools if t["function"]["name"] in valid_tools]
        executed_trades: List[Dict[str, Any]] = []
        plan_items = self._executable_plan_items(execution_plan)
        expected_calls = len(plan_items)
        matched_plan_items = 0

        for _ in range(self.EXECUTION_MAX_STEPS):
            self._log_llm_trace(
                current_sub_agent="ExecutionAgent",
                phase="request",
                payload={
                    "messages": messages,
                    "tools": stage_tools if stage_tools else None,
                },
            )
            resp = self.llm.call(messages, tools=stage_tools if stage_tools else None)
            msg_content = resp.content or ""
            tool_calls, tool_guard_warnings = LLMClient.apply_tool_call_guardrails(
                getattr(resp, "tool_calls", None),
                model=getattr(self.llm, "model", None),
            )
            if tool_guard_warnings:
                logger.warning("Tool-call guardrails triggered: %s", " | ".join(tool_guard_warnings))

            resp_dict = self.llm.build_assistant_message_dict(resp)
            if tool_calls:
                resp_dict["tool_calls"] = tool_calls
            else:
                resp_dict.pop("tool_calls", None)
            self._log_llm_trace(
                current_sub_agent="ExecutionAgent",
                phase="response",
                payload={
                    "message": resp_dict,
                    "tool_guard_warnings": tool_guard_warnings,
                },
            )
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
                    tc_id, name, tc_arguments = LLMClient.tool_call_parts(tc)
                    resolved_name = (
                        name.rsplit(":", 1)[-1] if isinstance(name, str) else name
                    )
                    is_execute_trade = resolved_name == "execute_trade"
                    tool_invoked = False
                    try:
                        args = json.loads(tc_arguments or "{}")
                    except Exception as e:
                        args = {}
                        result = {"error": f"Invalid tool arguments for {name}: {e}"}
                    else:
                        if not isinstance(args, dict):
                            args = {}
                            result = {
                                "error": f"Invalid tool arguments for {name}: expected a JSON object"
                            }
                        elif resolved_name not in allowed_tools:
                            result = {
                                "error": f"Tool not allowed in execution stage: {name}"
                            }
                        elif is_execute_trade and matched_plan_items >= expected_calls:
                            result = {
                                "error": "Execution plan is already complete; extra trade call rejected"
                            }
                        elif is_execute_trade and not self._execution_call_matches_plan_item(
                            plan_items[matched_plan_items], args
                        ):
                            expected_item = plan_items[matched_plan_items]
                            expected_signature = self._execution_command_signature(
                                expected_item,
                                plan_item=True,
                            )
                            received_signature = self._execution_command_signature(
                                args,
                                plan_item=False,
                            )
                            result = {
                                "error": "Trade call does not match the next execution plan item",
                                "expected": expected_signature,
                                "received": received_signature,
                            }
                        else:
                            tool_func = self.tools.get(name)
                            if tool_func is None:
                                result = {"error": f"Tool not found: {name}"}
                            else:
                                try:
                                    result = self._invoke_llm_tool(
                                        name,
                                        args,
                                        tool_call_id=tc_id,
                                        decision_round_id=decision_round_id,
                                    )
                                    tool_invoked = True
                                except Exception as e:
                                    result = {"error": f"Tool execution failed for {name}: {e}"}

                    if is_execute_trade and tool_invoked:
                        executed_trades.append(result if isinstance(result, dict) else {"raw_result": str(result)})
                        if self._execution_result_completed_plan_item(result):
                            matched_plan_items += 1

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                    messages.append(tool_msg)

                    if on_step:
                        on_step(
                            {
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "name": name,
                                "content": json.dumps(result, ensure_ascii=False),
                                "metadata": {"agent": "ExecutionAgent"},
                            }
                        )
                if tool_guard_warnings:
                    messages.append(LLMClient.tool_guardrail_warning_user_message(tool_guard_warnings))
                if expected_calls > 0 and matched_plan_items < expected_calls:
                    remaining = expected_calls - matched_plan_items
                    next_step = plan_items[matched_plan_items]
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
                if matched_plan_items == expected_calls:
                    return _ExecutionStageResult(
                        executed_trades,
                        matched_plan_items,
                        expected_calls,
                    )
                remaining = expected_calls - matched_plan_items
                next_step = plan_items[matched_plan_items]
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Termination denied: only {matched_plan_items}/{expected_calls} execution plan items completed. "
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

        if matched_plan_items < expected_calls:
            logger.warning(
                "Execution stage exited by step limit before completing plan: completed=%s expected=%s",
                matched_plan_items,
                expected_calls,
            )
        return _ExecutionStageResult(
            executed_trades,
            matched_plan_items,
            expected_calls,
        )

    def _run_sub_agent(
        self,
        agent_name: str,
        instruction: str,
        portfolio: Dict[str, Any],
        prices: Dict[str, Any],
        on_step: Optional[Callable] = None,
    ) -> str:
        if agent_name == "TradingAgent":
            prompt_slot = "trading"
            allowed_tools = ["get_market_snapshot", "get_kline_history", "get_account_state"]
        elif agent_name == "NewsAgent":
            prompt_slot = "news"
            allowed_tools = ["consult_search_agent"]
        elif agent_name == "CoderAgent":
            prompt_slot = "coder"
            allowed_tools = ["run_python_script", "read_file", "write_file", "execute_shell_command"]
        elif agent_name == "AnalystAgent":
            prompt_slot = "analyst"
            allowed_tools = []
        elif agent_name == "CriticAgent":
            prompt_slot = "critic"
            allowed_tools = []
        else:
            return f"Error: Unknown agent {agent_name}"

        available_tools_names = [t["function"]["name"] for t in self.tools.openai_tools]
        valid_tools = [t for t in allowed_tools if t in available_tools_names]

        variables = {"instruction": instruction}
        if prompt_slot in {"trading", "analyst", "critic"}:
            variables.update(
                portfolio=json.dumps(portfolio, ensure_ascii=False),
                prices=json.dumps(prices, ensure_ascii=False),
            )
        system_prompt = self.prompt_resolver.render_slot(
            self.PROMPT_PROFILE_ID,
            prompt_slot,
            variables,
        ).content
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
            self._log_llm_trace(
                current_sub_agent=agent_name,
                phase="request",
                payload={
                    "messages": messages,
                    "tools": current_tools if current_tools else None,
                },
            )
            resp = self.llm.call(messages, tools=current_tools if current_tools else None)

            msg_content = resp.content or ""
            tool_calls, tool_guard_warnings = LLMClient.apply_tool_call_guardrails(
                getattr(resp, "tool_calls", None),
                model=getattr(self.llm, "model", None),
            )
            if tool_guard_warnings:
                logger.warning("Tool-call guardrails triggered: %s", " | ".join(tool_guard_warnings))

            resp_dict = self.llm.build_assistant_message_dict(resp)
            if tool_calls:
                resp_dict["tool_calls"] = tool_calls
            else:
                resp_dict.pop("tool_calls", None)
            self._log_llm_trace(
                current_sub_agent=agent_name,
                phase="response",
                payload={
                    "message": resp_dict,
                    "tool_guard_warnings": tool_guard_warnings,
                },
            )
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
                    tc_id, name, tc_arguments = LLMClient.tool_call_parts(tc)
                    try:
                        args = json.loads(tc_arguments or "{}")
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
                                    prepared_args = self._prepare_news_search_args(args)
                                    try:
                                        result = tool_func(**prepared_args)
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
                        "tool_call_id": tc_id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                    messages.append(tool_msg)

                    if on_step:
                        on_step(
                            {
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "name": name,
                                "content": json.dumps(result, ensure_ascii=False),
                                "metadata": {"agent": agent_name},
                            }
                        )
                if tool_guard_warnings:
                    messages.append(LLMClient.tool_guardrail_warning_user_message(tool_guard_warnings))
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
            self._log_llm_trace(
                current_sub_agent=agent_name,
                phase="fallback_request",
                payload={"messages": fallback_messages, "tools": None},
            )
            resp = self.llm.call(fallback_messages)
            self._log_llm_trace(
                current_sub_agent=agent_name,
                phase="fallback_response",
                payload={"content": resp.content or ""},
            )
            current_response = resp.content or ""

        return current_response

    def run(
        self,
        portfolio: Dict[str, Any],
        prices: Dict[str, float],
        on_step: Optional[Callable[[Dict], None]] = None,
        trace_id: Optional[str] = None,
        decision_round_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        logger.info("Starting Advanced Multi-Agent decision process")

        self.context = []
        self.evidence_log = []

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
            self._log_llm_trace(
                current_sub_agent="Manager",
                phase="request",
                payload={"messages": manager_messages, "tools": None},
            )
            resp = self.llm.call(manager_messages)
            content = resp.content or ""
            self._log_llm_trace(
                current_sub_agent="Manager",
                phase="response",
                payload={"content": content},
            )

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
                candidate_plan = self._normalize_execution_plan(decision.get("execution_plan"))
                allow_partial = step >= max(0, self.max_steps - 3)
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

                execution_plan = list(candidate_plan)
                if not execution_plan and decision.get("final_decision"):
                    execution_plan = self._normalize_execution_plan([decision.get("final_decision")])

                execution_result = self._run_execution_stage(
                    execution_plan=execution_plan,
                    decision=decision,
                    portfolio=portfolio,
                    prices=prices,
                    on_step=on_step,
                    decision_round_id=decision_round_id,
                )
                executed_trades = execution_result.trades
                expected_execution_calls = execution_result.expected_plan_items
                execution_complete = execution_result.complete

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
                    "execution_complete": execution_complete,
                    "expected_execution_calls": expected_execution_calls,
                    "completed_execution_calls": execution_result.matched_plan_items,
                    "decision_basis": decision.get("decision_basis") or {},
                    "termination_reason": (
                        "max_steps"
                        if not execution_complete
                        else (
                            "trade_done"
                            if self._has_filled_trade(executed_trades)
                            else "hold"
                        )
                    ),
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
                "termination_reason": "max_steps",
            }

        return final_decision
