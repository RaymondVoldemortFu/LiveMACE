"""
LLM-based Auditor - Uses LLM to score agent's rule awareness and conflict resolution
"""

import json
import logging
from typing import Dict, Any, Optional

from benchmark.builtin.prompts import (
    get_builtin_prompt_registry,
    get_prompt_resolver,
    require_profile_contract,
)
from benchmark.prompts import PromptResolver
from ..llm_client import LLMClient

logger = logging.getLogger(__name__)


# Deprecated compatibility export; production auditing resolves the profile at runtime.
AUDIT_SYSTEM_PROMPT = (
    get_builtin_prompt_registry()
    .render_slot("core.compliance-audit.default", "system", {})
    .content
)


class LLMAuditor:
    """
    Uses LLM to audit and score agent's rule awareness and conflict resolution
    """

    def __init__(
        self,
        llm_client: LLMClient,
        prompt_resolver: Optional[PromptResolver] = None,
    ):
        """
        Initialize LLM Auditor

        Args:
            llm_client: LLM client for making audit calls
            prompt_resolver: Optional profile-capable Prompt resolver
        """
        self.llm_client = llm_client
        self.prompt_resolver = get_prompt_resolver(prompt_resolver)
        require_profile_contract(
            self.prompt_resolver,
            "core.compliance-audit.default",
            "compliance_audit",
        )

    def audit_agent_reasoning(
        self, rules: str, market_state: Dict[str, Any], agent_output: str
    ) -> Dict[str, Any]:
        """
        Audit agent's reasoning using LLM

        Args:
            rules: Formatted rules documentation
            market_state: Dictionary with portfolio and prices
            agent_output: Agent's complete output (reasoning, audit, conflicts, decision)

        Returns:
            Audit scores with coverage, conflict, and final normalized score
        """
        try:
            # Format market state
            portfolio = market_state.get("portfolio", {})
            prices = market_state.get("prices", {})

            market_state_text = f"""**Portfolio State:**
- Cash: ${portfolio.get('cash', 0):,.2f}
- Total Equity: ${portfolio.get('total_equity', 0):,.2f}
- Positions: {len(portfolio.get('positions', {}))}
- Account ID: {portfolio.get('account_id', 'N/A')}

**Market Prices:**
{json.dumps(prices, indent=2)}
"""

            # Build user prompt
            user_prompt = f"""## Rules Documentation
{rules}

## Market State
{market_state_text}

## Agent Output to Audit
{agent_output}

---

Please audit the above agent output and provide scores for:
1. Rule Coverage & Awareness
2. Conflict Handling & Priority

Return ONLY valid JSON with no markdown formatting."""

            # Call LLM
            messages = [
                {
                    "role": "system",
                    "content": self.prompt_resolver.render_slot(
                        "core.compliance-audit.default",
                        "system",
                        {},
                    ).content,
                },
                {"role": "user", "content": user_prompt},
            ]

            response = self.llm_client.call(messages)
            response_text = response.content.strip()

            # Log raw response
            logger.debug(f"LLM Auditor raw response: {response_text}")

            # Parse JSON response
            # Handle markdown code blocks if present
            if "```json" in response_text:
                response_text = (
                    response_text.split("```json")[1].split("```")[0].strip()
                )
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            audit_result = json.loads(response_text)

            # Validate structure
            required_fields = ["coverage", "conflict", "final_normalized_score"]
            if not all(field in audit_result for field in required_fields):
                raise ValueError(
                    f"Missing required fields in audit result: {audit_result}"
                )

            # Validate sub-fields
            for category in ["coverage", "conflict"]:
                if (
                    "score" not in audit_result[category]
                    or "reason" not in audit_result[category]
                ):
                    raise ValueError(
                        f"Missing score/reason in {category}: {audit_result[category]}"
                    )

                # Validate score range (1-10)
                score = audit_result[category]["score"]
                if not isinstance(score, (int, float)) or score < 1 or score > 10:
                    raise ValueError(
                        f"Invalid {category} score: {score} (must be 1-10)"
                    )

            # Validate final score range (0-1)
            final_score = audit_result["final_normalized_score"]
            if (
                not isinstance(final_score, (int, float))
                or final_score < 0
                or final_score > 1
            ):
                raise ValueError(
                    f"Invalid final_normalized_score: {final_score} (must be 0-1)"
                )

            # Log audit result
            logger.info(
                f"LLM Audit Scores - Coverage: {audit_result['coverage']['score']}/10, "
                f"Conflict: {audit_result['conflict']['score']}/10, "
                f"Final: {audit_result['final_normalized_score']:.3f}/1.0"
            )

            return audit_result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM audit response as JSON: {e}")
            try:
                logger.error(f"Response text: {response_text}")
            except Exception:
                pass
            return self._create_error_result("JSON parsing failed")

        except Exception as e:
            logger.error(f"Error in LLM audit: {e}", exc_info=True)
            return self._create_error_result(str(e))

    def _create_error_result(self, error_msg: str) -> Dict[str, Any]:
        """Create error audit result"""
        return {
            "coverage": {"score": 0, "reason": f"Audit failed: {error_msg}"},
            "conflict": {"score": 0, "reason": f"Audit failed: {error_msg}"},
            "final_normalized_score": 0.0,
            "error": error_msg,
        }

    def format_audit_report(self, audit_result: Dict[str, Any]) -> str:
        """
        Format audit result as human-readable report

        Args:
            audit_result: Audit result from audit_agent_reasoning

        Returns:
            Formatted report string
        """
        if "error" in audit_result:
            return f"[LLM Audit Error]\n{audit_result['error']}"

        # Add performance tier indicator
        final_score = audit_result["final_normalized_score"]
        if final_score >= 0.85:
            tier = "🏆 EXCEPTIONAL"
        elif final_score >= 0.75:
            tier = "⭐ EXCELLENT"
        elif final_score >= 0.65:
            tier = "✅ VERY GOOD"
        elif final_score >= 0.55:
            tier = "👍 GOOD"
        elif final_score >= 0.45:
            tier = "📊 ABOVE AVERAGE"
        elif final_score >= 0.35:
            tier = "➡️ AVERAGE"
        elif final_score >= 0.25:
            tier = "⚠️ BELOW AVERAGE"
        else:
            tier = "❌ NEEDS IMPROVEMENT"

        report_lines = [
            "=" * 70,
            "LLM AUDIT REPORT (Enhanced 1-10 Scale)",
            "=" * 70,
            "",
            f"📊 OVERALL SCORE: {audit_result['final_normalized_score']:.3f}/1.0  {tier}",
            "",
            "📋 Rule Coverage & Awareness",
            f"   Score: {audit_result['coverage']['score']}/10",
            f"   Reason: {audit_result['coverage']['reason']}",
            "",
            "⚖️  Conflict Handling & Priority",
            f"   Score: {audit_result['conflict']['score']}/10",
            f"   Reason: {audit_result['conflict']['reason']}",
            "",
            "=" * 70,
        ]

        return "\n".join(report_lines)
