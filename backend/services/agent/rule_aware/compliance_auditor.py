"""
Compliance Auditor - Audits agent decisions and tracks rule compliance
"""
import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime

from .rule_engine import RuleEngine
from .rule_validator import RuleValidator, RuleViolation

logger = logging.getLogger(__name__)


class ComplianceAudit:
    """Represents a compliance audit record"""
    
    def __init__(self):
        self.timestamp = datetime.utcnow()
        self.rules_checked: List[str] = []  # List of rule IDs checked
        self.violations: List[RuleViolation] = []  # Rule violations found
        self.conflicts: List[Dict[str, Any]] = []  # Rule conflicts
        self.adjustments: List[Dict[str, Any]] = []  # Adjustments made
        self.final_status: str = "PENDING"  # PASS, FAIL, ADJUSTED
        self.decision_before: Optional[Dict] = None
        self.decision_after: Optional[Dict] = None
        self.s_rule_sat: Optional[float] = None  # Rule satisfaction score (0-1)
        self.r2_results: Optional[Dict[str, Any]] = None  # R2 soft rule results
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert audit to dictionary"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "rules_checked": self.rules_checked,
            "violations": [v.to_dict() for v in self.violations],
            "conflicts": self.conflicts,
            "adjustments": self.adjustments,
            "final_status": self.final_status,
            "decision_before": self.decision_before,
            "decision_after": self.decision_after,
            "s_rule_sat": self.s_rule_sat,  # Rule satisfaction score
            "r2_results": self.r2_results  # R2 soft rule evaluation results
        }
    
    def format_for_output(self) -> str:
        """Format audit as human-readable text"""
        lines = []
        lines.append("[Compliance Audit]")
        
        # Rules checked
        if self.rules_checked:
            lines.append(f"\nRules Checked: {len(self.rules_checked)}")
            for rule_id in self.rules_checked:
                lines.append(f"  - {rule_id}")
        
        # Violations
        if self.violations:
            lines.append(f"\nViolations Found: {len(self.violations)}")
            for v in self.violations:
                lines.append(f"  - [{v.severity}] {v.rule.id}: {v.message}")
        else:
            lines.append("\nNo violations found ✓")
        
        # Conflicts
        if self.conflicts:
            lines.append(f"\nRule Conflicts: {len(self.conflicts)}")
            for conflict in self.conflicts:
                # Handle both old format (rule_a/rule_b) and new format (conflict string)
                if 'rule_a' in conflict and 'rule_b' in conflict:
                    lines.append(f"  - {conflict['rule_a']} vs {conflict['rule_b']}")
                elif 'conflict' in conflict:
                    lines.append(f"  - {conflict['conflict']}")
                else:
                    lines.append(f"  - {conflict}")
                
                if 'chosen' in conflict:
                    lines.append(f"    Chosen: {conflict['chosen']}")
                if 'reason' in conflict:
                    lines.append(f"    Reason: {conflict['reason']}")
        
        # Adjustments
        if self.adjustments:
            lines.append(f"\nAdjustments Made: {len(self.adjustments)}")
            for adj in self.adjustments:
                lines.append(f"  - {adj['field']}: {adj['from']} → {adj['to']}")
                lines.append(f"    Reason: {adj['reason']}")
        
        lines.append(f"\nFinal Status: {self.final_status}")
        
        return "\n".join(lines)


class ComplianceAuditor:
    """
    Audits agent decisions for rule compliance
    Tracks compliance history and generates audit trails
    """
    
    def __init__(self, rule_engine: RuleEngine, rule_validator: RuleValidator):
        self.rule_engine = rule_engine
        self.rule_validator = rule_validator
        self.audit_history: List[ComplianceAudit] = []

    def post_execution_portfolio(self, portfolio, prices, executed_trades=()):
        """Read committed balances and positions together after tool execution."""
        from database.connection import SessionLocal
        from database.models import Account, Order, Position, Trade
        from services.asset_calculator import calculate_position_market_value

        account_id = portfolio.get("account_id")
        with SessionLocal() as db:
            account = db.get(Account, account_id)
            if account is None:
                raise ValueError("Post-execution audit account is unavailable")
            positions = (
                db.query(Position)
                .filter(Position.account_id == account_id, Position.quantity > 0)
                .all()
            )
            order_ids = set()
            for trade in executed_trades:
                result = trade.get("result") or {}
                if result.get("executed") is not True or result.get("error"):
                    continue
                for item in [result, *(result.get("closed_orders") or [])]:
                    if item.get("order_id") is not None:
                        order_ids.add(item["order_id"])
            executed_order_fills = {}
            if order_ids:
                fills = db.query(Trade, Order.leverage).join(Order, Trade.order_id == Order.id).filter(
                    Trade.account_id == account_id, Trade.order_id.in_(order_ids)
                ).all()
                for fill, leverage in fills:
                    record = executed_order_fills.setdefault(str(fill.order_id), {
                        "symbol": fill.symbol, "market": fill.market,
                        "leverage": leverage, "notional_usd": 0.0,
                    })
                    record["notional_usd"] += abs(float(fill.price) * float(fill.quantity))
            cash = float(account.current_cash)
            equity = cash
            snapshots = []
            for position in positions:
                price = float(prices.get(position.symbol) or position.avg_cost)
                equity += float(calculate_position_market_value(position, price))
                snapshots.append(
                    {
                        "symbol": position.symbol,
                        "market": position.market,
                        "quantity": float(position.quantity),
                        "avg_cost": float(position.avg_cost),
                        "leverage": position.leverage,
                        "side": position.side,
                        "valuation_price": price,
                    }
                )
            return {
                "account_id": account_id,
                "decision_round_id": portfolio.get("decision_round_id"),
                "cash": cash,
                "frozen_cash": float(account.frozen_cash),
                "margin_used": float(account.margin_used),
                "total_assets": equity,
                "total_equity": equity,
                "positions": snapshots,
                "executed_order_fills": executed_order_fills,
                "audit_phase": "post_execution",
                "price_basis": "decision_round_quotes_with_entry_cost_fallback",
            }

    @staticmethod
    def _executed_actions(decision, portfolio):
        """Expand batched closes and audit normalized fills instead of request aliases."""
        actions = []
        seen_orders = set()
        fills = portfolio.get("executed_order_fills", {})
        for trade in decision.get("executed_trades", []):
            result = trade.get("result") or {}
            args = trade.get("args") or {}
            if result.get("executed") is not True or result.get("error"):
                continue
            operation = str(result.get("operation") or args.get("operation") or "").lower()
            if operation == "hold":
                continue
            items = result.get("closed_orders", []) if operation == "close_all" else [result]
            for item in items:
                order_id = str(item["order_id"]) if item.get("order_id") is not None else None
                if order_id in seen_orders:
                    continue
                fill = fills.get(order_id, {})
                action = {**args, **item, **fill}
                action["operation"] = "close" if operation == "close_all" else "open" if operation == "all_in" else operation
                notional = fill.get("notional_usd", item.get("notional_usd"))
                if notional is None:
                    raise ValueError("Executed order notional is unavailable for compliance audit")
                action.update(size_mode="usd", usd_amount=float(notional))
                equity = portfolio["total_equity"]
                action["target_portion_of_balance"] = float(notional) / equity if equity > 0 else 0
                actions.append(action)
                if order_id is not None:
                    seen_orders.add(order_id)
        return actions or [{"operation": "hold"}]

    def audit_decision(
        self, 
        decision: Dict[str, Any], 
        portfolio: Dict[str, Any], 
        prices: Dict[str, float],
        agent_reasoning: Optional[Dict[str, Any]] = None
    ) -> ComplianceAudit:
        """
        Perform full compliance audit on a decision
        
        Args:
            decision: The trading decision
            portfolio: Current portfolio state
            prices: Market prices
            agent_reasoning: Optional agent's reasoning output
        
        Returns:
            ComplianceAudit object with results
        """
        audit = ComplianceAudit()
        audit.decision_before = decision.copy()
        
        # Get all rules to check
        all_rules = self.rule_engine.get_all_rules()
        audit.rules_checked = [rule.id for rule in all_rules]
        
        # Validate decision
        decisions = [decision]
        if portfolio.get("audit_phase") == "post_execution":
            decisions = self._executed_actions(decision, portfolio)
        violations = []
        for action in decisions:
            _, action_violations = self.rule_validator.validate_decision(action, portfolio, prices)
            for violation in action_violations:
                if not any(existing.to_dict() == violation.to_dict() for existing in violations):
                    violations.append(violation)
        audit.violations = violations
        
        # Extract conflicts from agent reasoning if provided
        if agent_reasoning and "conflicts" in agent_reasoning:
            audit.conflicts = agent_reasoning["conflicts"]
        
        # Calculate R2 (soft) rule scores
        # Import RuleLevel enum to properly filter R2 rules
        from .rule_engine import RuleLevel
        
        r2_rules = [r for r in all_rules if r.level == RuleLevel.R2_CLIENT_SOFT]
        logger.info(f"Found {len(r2_rules)} R2 rules out of {len(all_rules)} total rules")
        r2_scores = {}
        r2_total_score = 0.0
        r2_rule_count = 0
        
        for rule in r2_rules:
            # For R2 rules, check if there's a violation
            rule_violations = [v for v in violations if v.rule.id == rule.id]
            if not rule_violations:
                # No violation = full score
                r2_scores[rule.id] = 1.0
                r2_total_score += 1.0
            else:
                # Has violation: use continuous score if available
                violation = min(rule_violations, key=lambda item: item.score if item.score is not None else 0.0)
                if violation.score is not None:
                    # Use continuous score (0.0 - 1.0)
                    rule_score = max(0.0, min(1.0, violation.score))
                    r2_scores[rule.id] = rule_score
                    r2_total_score += rule_score
                else:
                    # Fallback to severity-based scoring for backward compatibility
                    if violation.severity == "WARNING":
                        r2_scores[rule.id] = 0.5  # 50% for warnings
                        r2_total_score += 0.5
                    else:
                        r2_scores[rule.id] = 0.0  # 0% for critical
                        r2_total_score += 0.0
            r2_rule_count += 1
        
        # Store R2 results
        audit.r2_results = {
            "rule_scores": r2_scores,
            "average_score": r2_total_score / r2_rule_count if r2_rule_count > 0 else 1.0,
            "total_rules": r2_rule_count
        }
        
        # Calculate overall rule satisfaction score (s_rule_sat)
        # s_rule_sat is based on R2 soft rule scores, but forced to 0 if R0/R1 violations exist
        
        # Check for hard constraint violations (R0 or R1)
        r0_violations = [v for v in violations if v.rule.id.startswith("R0")]
        r1_violations = [v for v in violations if v.rule.id.startswith("R1")]
        has_hard_violations = len(r0_violations) > 0 or len(r1_violations) > 0
        
        if has_hard_violations:
            # Hard constraint violation (R0/R1) -> force s_rule_sat to 0
            audit.s_rule_sat = 0.0
            logger.info(f"Rule satisfaction score: 0.000 (hard constraint violations: R0={len(r0_violations)}, R1={len(r1_violations)})")
        else:
            # No hard violations -> s_rule_sat equals R2 average score
            audit.s_rule_sat = audit.r2_results["average_score"]
            logger.info(f"Rule satisfaction score: {audit.s_rule_sat:.3f} (R2 average score, no hard violations)")
        
        # Determine status
        critical_violations = [v for v in violations if v.severity == "CRITICAL"]
        warning_violations = [v for v in violations if v.severity == "WARNING"]
        
        if critical_violations:
            audit.final_status = "FAIL"
        elif warning_violations:
            audit.final_status = "ADJUSTED"
        else:
            audit.final_status = "PASS"
        
        audit.decision_after = decision.copy()
        
        # Store in history
        self.audit_history.append(audit)
        
        return audit
    
    def parse_agent_output(self, agent_output: str) -> Dict[str, Any]:
        """
        Parse structured agent output to extract compliance information
        
        Expected format:
        [Reasoning & Market View]
        ...
        [Compliance Audit]
        - Rule [ID]: [Status] | [Note]
        ...
        [Conflict Resolution]
        - Conflict: [ID] vs [ID]
        - Chosen: [ID]
        - Reason: ...
        [Final Action]
        {...json...}
        
        Returns:
            Parsed structure with reasoning, audit, conflicts, and decision
        """
        result = {
            "reasoning": "",
            "audit_items": [],
            "conflicts": [],
            "decision": None
        }
        
        sections = {
            "reasoning": "",
            "audit": "",
            "conflicts": "",
            "action": ""
        }
        
        current_section = None
        lines = agent_output.split('\n')
        
        for line in lines:
            line_lower = line.lower().strip()
            
            if '[reasoning' in line_lower or '[market view' in line_lower:
                current_section = "reasoning"
            elif '[compliance audit' in line_lower:
                current_section = "audit"
            elif '[conflict' in line_lower:
                current_section = "conflicts"
            elif '[final action' in line_lower or '[action' in line_lower:
                current_section = "action"
            elif current_section:
                sections[current_section] += line + "\n"
        
        # Parse reasoning
        result["reasoning"] = sections["reasoning"].strip()
        
        # Parse audit items
        for line in sections["audit"].split('\n'):
            if line.strip().startswith('- Rule'):
                # Parse format: - Rule [R0-01]: [Pass] | Note
                try:
                    parts = line.split(':')
                    if len(parts) >= 2:
                        rule_id = parts[0].split('[')[1].split(']')[0]
                        rest = ':'.join(parts[1:])
                        status_parts = rest.split('|')
                        status = status_parts[0].strip().strip('[]')
                        note = status_parts[1].strip() if len(status_parts) > 1 else ""
                        
                        result["audit_items"].append({
                            "rule_id": rule_id,
                            "status": status,
                            "note": note
                        })
                except Exception as e:
                    logger.debug(f"Failed to parse audit line: {line}, error: {e}")
        
        # Parse conflicts
        conflict_lines = sections["conflicts"].split('\n')
        current_conflict = {}
        for line in conflict_lines:
            line = line.strip()
            if line.startswith('- Conflict:'):
                if current_conflict:
                    result["conflicts"].append(current_conflict)
                current_conflict = {"conflict": line.replace('- Conflict:', '').strip()}
            elif line.startswith('- Chosen:'):
                current_conflict["chosen"] = line.replace('- Chosen:', '').strip()
            elif line.startswith('- Reason:'):
                current_conflict["reason"] = line.replace('- Reason:', '').strip()
        
        if current_conflict:
            result["conflicts"].append(current_conflict)
        
        # Parse decision JSON
        try:
            # Look for JSON in action section
            json_start = sections["action"].find('{')
            json_end = sections["action"].rfind('}')
            if json_start != -1 and json_end != -1:
                json_str = sections["action"][json_start:json_end+1]
                result["decision"] = json.loads(json_str)
        except Exception as e:
            logger.warning(f"Failed to parse decision JSON: {e}")
        
        return result
    
    def get_compliance_stats(self) -> Dict[str, Any]:
        """Get compliance statistics from audit history"""
        if not self.audit_history:
            return {
                "total_audits": 0,
                "pass_rate": 0.0,
                "fail_rate": 0.0,
                "adjustment_rate": 0.0
            }
        
        total = len(self.audit_history)
        passed = sum(1 for a in self.audit_history if a.final_status == "PASS")
        failed = sum(1 for a in self.audit_history if a.final_status == "FAIL")
        adjusted = sum(1 for a in self.audit_history if a.final_status == "ADJUSTED")
        
        return {
            "total_audits": total,
            "pass_count": passed,
            "fail_count": failed,
            "adjusted_count": adjusted,
            "pass_rate": passed / total,
            "fail_rate": failed / total,
            "adjustment_rate": adjusted / total
        }
    
    def get_most_violated_rules(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """Get the most frequently violated rules"""
        violation_counts = {}
        
        for audit in self.audit_history:
            for violation in audit.violations:
                rule_id = violation.rule.id
                if rule_id not in violation_counts:
                    violation_counts[rule_id] = {
                        "rule_id": rule_id,
                        "rule_name": violation.rule.name,
                        "count": 0,
                        "severity": violation.severity
                    }
                violation_counts[rule_id]["count"] += 1
        
        # Sort by count
        sorted_violations = sorted(
            violation_counts.values(), 
            key=lambda x: x["count"], 
            reverse=True
        )
        
        return sorted_violations[:top_n]
