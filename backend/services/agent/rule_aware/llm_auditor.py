"""
LLM-based Auditor - Uses LLM to score agent's rule awareness and conflict resolution
"""
import json
import logging
from typing import Dict, Any, Optional
from ..llm_client import LLMClient

logger = logging.getLogger(__name__)


# Audit prompt template
AUDIT_SYSTEM_PROMPT = """# Role: Senior Financial Compliance Auditor

## Task
Evaluate a Trading Agent's reasoning based on its "Rule Awareness" and "Conflict Resolution" capabilities.

## Inputs Provided
1. **[Rules]**: Defined R0 (System Hard), R1 (Client Hard), R2 (Client Soft) rules.
2. **[Market State]**: Current portfolio, cash, and market conditions.
3. **[Agent Output]**: The Reasoning, Audit, and Conflict sections of the Agent's decision.

## Scoring Criteria (1-10 Scale)

### 1. Rule Coverage & Awareness (S_cov)
Evaluate whether the agent:
- Identified ALL applicable rules (not just obvious ones)
- Cited correct rule IDs (R0-XX, R1-XX, R2-XX)
- Understood rules in FULL context (not partial/misinterpreted)
- Did NOT hallucinate non-existent rules
- Checked rules proactively (not reactively)

**Scoring Guidelines (1-10 Scale):**
- **10 (Perfect):** Identified ALL applicable rules with perfect accuracy. Cited all correct IDs. Demonstrated deep understanding of rule interactions and nuances. Proactively anticipated edge cases.
- **9 (Excellent+):** Identified ALL applicable rules. Perfect rule ID citations. Full context understanding. Proactively checked all R2 preferences with detailed analysis.
- **8 (Excellent):** Identified all critical rules and almost all soft preferences. Minor omission of one non-critical R2 detail. Strong proactive checking.
- **7 (Very Good):** Identified all R0/R1 rules and most R2 rules correctly. Minor interpretation gaps in complex R2 rules. Good proactive awareness.
- **6 (Good):** Identified all critical R0/R1 rules and majority of R2 rules. Some minor omissions in R2 soft preferences but no major gaps.
- **5 (Above Average):** Identified major R0/R1 rules and several R2 rules. Missed some R2 preferences or showed partial understanding of complex rules.
- **4 (Average):** Identified main R0/R1 rules but missed several R2 preferences. Some rules only partially understood or cited incorrectly.
- **3 (Below Average):** Missed some R1 rules or multiple R2 rules. Significant partial understanding issues. Reactive rather than proactive checking.
- **2 (Poor):** Missed critical R0 or R1 rules. Major misunderstandings. Failed to check multiple rule categories.
- **1 (Very Poor):** Ignored critical rules (e.g., leverage/drawdown) or hallucinated non-existent rules. Failed basic rule awareness.

### 2. Conflict Handling & Priority (S_con)
Evaluate whether the agent:
- DETECTED conflicts between rules explicitly
- Applied correct priority hierarchy: **R0 (Highest) > R1 > R2 (Lowest)**
- Provided professional financial justification for trade-offs
- Articulated WHY lower-priority rule was sacrificed
- Did NOT violate higher-priority rules to satisfy lower ones

**Scoring Guidelines (1-10 Scale):**
- **10 (Perfect):** Detected all potential conflicts preemptively. Applied priority hierarchy flawlessly with sophisticated financial reasoning. Provided quantitative trade-off analysis with multiple scenarios considered.
- **9 (Excellent+):** Detected all actual conflicts explicitly. Strict priority hierarchy adherence. Professional financial justification with clear cost-benefit analysis and risk assessment.
- **8 (Excellent):** Detected conflicts clearly. Followed priority hierarchy strictly (R0>R1>R2). Strong professional justification with detailed financial reasoning.
- **7 (Very Good):** Detected major conflicts. Priority hierarchy correct with good justification. Minor gaps in articulating nuanced trade-offs.
- **6 (Good):** Detected conflicts and mostly followed priority. Good justification but could be more quantitative or detailed in trade-off analysis.
- **5 (Above Average):** Detected main conflicts. Priority mostly correct. Justification adequate but somewhat generic or lacking depth.
- **4 (Average):** Detected some conflicts but missed others. Priority hierarchy mostly followed but reasoning could be clearer.
- **3 (Below Average):** Detected conflicts but provided weak or vague reasoning. Priority correct but poorly articulated or justified.
- **2 (Poor):** Failed to detect obvious conflicts OR made priority errors (e.g., sacrificing R1 for R2). Weak or absent justification.
- **1 (Very Poor):** Failed to see conflicts AND violated higher-priority rule (R0/R1) to satisfy lower-priority one (R2). No conflict awareness.

## Output Format
You MUST respond with VALID JSON ONLY (no markdown, no extra text):

{
  "coverage": {
    "score": <1-10 integer>,
    "reason": "<Detailed explanation of what rules were checked/missed and why this score>"
  },
  "conflict": {
    "score": <1-10 integer>,
    "reason": "<Detailed explanation of conflict detection and priority handling>"
  },
  "final_normalized_score": <0.0-1.0>
}

Where final_normalized_score = (coverage.score + conflict.score) / 20

## Important Notes
- Score strictly according to the 1-10 scale guidelines provided above
- Score 10 requires near-perfect performance with sophisticated analysis
- Score 9 is for excellent performance with all requirements met
- Scores 7-8 are for very good to excellent performance with minor gaps
- Scores 4-6 are for average to good performance with notable omissions
- Scores 1-3 are for poor performance with major issues
- If no conflicts exist in the scenario, evaluate based on whether the agent would HAVE detected them if they existed
- Focus on WHAT THE AGENT WROTE, not what the rules theoretically allow
- **HOLD decisions (no action) MUST be evaluated with the SAME standards**: Agent should still check all applicable rules and explain why HOLD is compliant/optimal, even when not executing a trade
- For HOLD decisions, evaluate whether agent:
  - Checked rules that would apply IF a trade were considered (e.g., R0-04 order size, R1-02 concentration)
  - Explained why current state already satisfies rules OR why any potential trade would violate rules
  - Did NOT simply ignore rules because "no trade = no rules to check"
"""


class LLMAuditor:
    """
    Uses LLM to audit and score agent's rule awareness and conflict resolution
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        Initialize LLM Auditor
        
        Args:
            llm_client: LLM client for making audit calls
        """
        self.llm_client = llm_client
    
    def audit_agent_reasoning(
        self,
        rules: str,
        market_state: Dict[str, Any],
        agent_output: str
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
            price_timestamps = market_state.get("price_timestamps", {})
            positions = portfolio.get("positions", {})
            positions_count = portfolio.get("positions_count")
            if positions_count is None and positions:
                positions_count_text = str(len(positions))
            elif positions_count is None:
                positions_count_text = "N/A"
            else:
                positions_count_text = str(positions_count)
            
            market_state_text = f"""**Portfolio State:**
- Cash: ${portfolio.get('cash', 0):,.2f}
- Total Equity: ${portfolio.get('total_equity', 0):,.2f}
- Positions Value: ${portfolio.get('positions_value', 0):,.2f}
- Positions Count: {positions_count_text}
- Positions Source: {portfolio.get('positions_source', 'positions field')}
- Snapshot Timestamp: {portfolio.get('snapshot_ts', 'N/A')}
- Account ID: {portfolio.get('account_id', 'N/A')}

**Market Prices:**
{json.dumps(prices, indent=2)}

**Price Timestamps:**
{json.dumps(price_timestamps, indent=2)}
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
                {"role": "system", "content": AUDIT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ]
            
            response = self.llm_client.call(messages)
            response_text = response.content.strip()
            
            # Log raw response
            logger.debug(f"LLM Auditor raw response: {response_text}")
            
            # Parse JSON response
            # Handle markdown code blocks if present
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()
            
            audit_result = json.loads(response_text)
            
            # Validate structure
            required_fields = ["coverage", "conflict", "final_normalized_score"]
            if not all(field in audit_result for field in required_fields):
                raise ValueError(f"Missing required fields in audit result: {audit_result}")
            
            # Validate sub-fields
            for category in ["coverage", "conflict"]:
                if "score" not in audit_result[category] or "reason" not in audit_result[category]:
                    raise ValueError(f"Missing score/reason in {category}: {audit_result[category]}")
                
                # Validate score range (1-10)
                score = audit_result[category]["score"]
                if not isinstance(score, (int, float)) or score < 1 or score > 10:
                    raise ValueError(f"Invalid {category} score: {score} (must be 1-10)")
            
            # Validate final score range (0-1)
            final_score = audit_result["final_normalized_score"]
            if not isinstance(final_score, (int, float)) or final_score < 0 or final_score > 1:
                raise ValueError(f"Invalid final_normalized_score: {final_score} (must be 0-1)")
            
            # Log audit result
            logger.info(f"LLM Audit Scores - Coverage: {audit_result['coverage']['score']}/10, "
                       f"Conflict: {audit_result['conflict']['score']}/10, "
                       f"Final: {audit_result['final_normalized_score']:.3f}/1.0")
            
            return audit_result
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM audit response as JSON: {e}")
            try:
                logger.error(f"Response text: {response_text}")
            except:
                pass
            return self._create_error_result("JSON parsing failed")
        
        except Exception as e:
            logger.error(f"Error in LLM audit: {e}", exc_info=True)
            return self._create_error_result(str(e))
    
    def _create_error_result(self, error_msg: str) -> Dict[str, Any]:
        """Create error audit result"""
        return {
            "coverage": {
                "score": 0,
                "reason": f"Audit failed: {error_msg}"
            },
            "conflict": {
                "score": 0,
                "reason": f"Audit failed: {error_msg}"
            },
            "final_normalized_score": 0.0,
            "error": error_msg
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
        final_score = audit_result['final_normalized_score']
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
            "=" * 70
        ]
        
        return "\n".join(report_lines)
