"""
Rule Validator - Validates trading decisions against rules
"""
import logging
from typing import Dict, Any, List, Tuple, Optional
from decimal import Decimal

from .rule_engine import RuleEngine, Rule, RuleLevel

logger = logging.getLogger(__name__)


class RuleViolation:
    """Represents a rule violation"""
    
    def __init__(self, rule: Rule, severity: str, message: str, actual_value: Any = None, expected_value: Any = None):
        self.rule = rule
        self.severity = severity  # "CRITICAL", "WARNING", "INFO"
        self.message = message
        self.actual_value = actual_value
        self.expected_value = expected_value
    
    def __repr__(self):
        return f"RuleViolation({self.rule.id}, {self.severity}: {self.message})"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule.id,
            "rule_name": self.rule.name,
            "severity": self.severity,
            "message": self.message,
            "actual_value": self.actual_value,
            "expected_value": self.expected_value
        }


class RuleValidator:
    """
    Validates trading decisions and portfolio states against rules
    """
    
    def __init__(self, rule_engine: RuleEngine):
        self.rule_engine = rule_engine
    
    def validate_decision(
        self, 
        decision: Dict[str, Any], 
        portfolio: Dict[str, Any], 
        prices: Dict[str, float]
    ) -> Tuple[bool, List[RuleViolation]]:
        """
        Validate a trading decision against all rules
        
        Args:
            decision: The trading decision to validate
            portfolio: Current portfolio state
            prices: Current market prices
        
        Returns:
            (is_valid, violations) - is_valid is False if any hard rule is violated
        """
        violations = []
        
        # Validate against R0 rules (System Hard)
        r0_violations = self._validate_r0_rules(decision, portfolio, prices)
        violations.extend(r0_violations)
        
        # Validate against R1 rules (Client Hard)
        r1_violations = self._validate_r1_rules(decision, portfolio, prices)
        violations.extend(r1_violations)
        
        # Validate against R2 rules (Client Soft)
        r2_violations = self._validate_r2_rules(decision, portfolio, prices)
        violations.extend(r2_violations)
        
        # Decision is invalid if any R0 or R1 rule is violated
        has_hard_violation = any(
            v.severity == "CRITICAL" for v in violations
        )
        
        return not has_hard_violation, violations
    
    def _validate_r0_rules(
        self, 
        decision: Dict[str, Any], 
        portfolio: Dict[str, Any], 
        prices: Dict[str, float]
    ) -> List[RuleViolation]:
        """Validate R0 (System Hard) rules"""
        violations = []
        r0_rules = self.rule_engine.get_rules_by_level(RuleLevel.R0_SYSTEM_HARD)
        
        for rule in r0_rules:
            try:
                violation = self._check_rule(rule, decision, portfolio, prices, severity="CRITICAL")
                if violation:
                    violations.append(violation)
            except Exception as e:
                logger.error(f"Error validating rule {rule.id}: {e}")
        
        return violations
    
    def _validate_r1_rules(
        self, 
        decision: Dict[str, Any], 
        portfolio: Dict[str, Any], 
        prices: Dict[str, float]
    ) -> List[RuleViolation]:
        """Validate R1 (Client Hard) rules"""
        violations = []
        r1_rules = self.rule_engine.get_rules_by_level(RuleLevel.R1_CLIENT_HARD)
        
        for rule in r1_rules:
            try:
                violation = self._check_rule(rule, decision, portfolio, prices, severity="CRITICAL")
                if violation:
                    violations.append(violation)
            except Exception as e:
                logger.error(f"Error validating rule {rule.id}: {e}")
        
        return violations
    
    def _validate_r2_rules(
        self, 
        decision: Dict[str, Any], 
        portfolio: Dict[str, Any], 
        prices: Dict[str, float]
    ) -> List[RuleViolation]:
        """Validate R2 (Client Soft) rules"""
        violations = []
        r2_rules = self.rule_engine.get_rules_by_level(RuleLevel.R2_CLIENT_SOFT)
        
        for rule in r2_rules:
            try:
                violation = self._check_rule(rule, decision, portfolio, prices, severity="WARNING")
                if violation:
                    violations.append(violation)
            except Exception as e:
                logger.error(f"Error validating rule {rule.id}: {e}")
        
        return violations
    
    def _check_rule(
        self, 
        rule: Rule, 
        decision: Dict[str, Any], 
        portfolio: Dict[str, Any], 
        prices: Dict[str, float],
        severity: str
    ) -> Optional[RuleViolation]:
        """
        Check a specific rule against the decision
        Returns RuleViolation if violated, None otherwise
        """
        rule_id = rule.id
        params = rule.parameters
        
        # R0-01: Maximum Leverage Limit
        if rule_id == "R0-01":
            max_leverage = params.get("max_leverage", 5)
            decision_leverage = decision.get("leverage", 1)
            if decision_leverage > max_leverage:
                return RuleViolation(
                    rule, severity,
                    f"Leverage {decision_leverage}x exceeds maximum {max_leverage}x",
                    actual_value=decision_leverage,
                    expected_value=f"<= {max_leverage}"
                )
        
        # R0-02: Maintenance Margin
        elif rule_id == "R0-02":
            min_margin_level = params.get("min_margin_level", 0.10)
            # Calculate projected margin level after this decision
            # (This is a simplified check - real implementation would need full calculation)
            cash = portfolio.get("cash", 0)
            total_assets = portfolio.get("total_assets", cash)
            if total_assets > 0:
                margin_level = cash / total_assets
                if margin_level < min_margin_level:
                    return RuleViolation(
                        rule, severity,
                        f"Margin level {margin_level:.2%} below minimum {min_margin_level:.2%}",
                        actual_value=margin_level,
                        expected_value=f">= {min_margin_level}"
                    )
        
        # R0-04: Maximum Single Order Notional Value
        elif rule_id == "R0-04":
            max_order_pct = params.get("max_order_pct", 0.20)
            symbol = decision.get("symbol")
            target_portion = decision.get("target_portion_of_balance", 0)
            
            if target_portion > max_order_pct:
                return RuleViolation(
                    rule, severity,
                    f"Order size {target_portion:.2%} exceeds maximum {max_order_pct:.2%} of portfolio",
                    actual_value=target_portion,
                    expected_value=f"<= {max_order_pct}"
                )
        
        # R1-01: Asset Blacklist
        elif rule_id == "R1-01":
            blacklist = params.get("blacklist_symbols", [])
            symbol = decision.get("symbol")
            if symbol in blacklist:
                return RuleViolation(
                    rule, severity,
                    f"Symbol {symbol} is in blacklist",
                    actual_value=symbol,
                    expected_value="Not in blacklist"
                )
        
        # R1-02: No Short Selling
        elif rule_id == "R1-02":
            if params.get("no_short_selling", False):
                direction = decision.get("direction", "long")
                if direction == "short":
                    return RuleViolation(
                        rule, severity,
                        "Short selling is prohibited",
                        actual_value="short",
                        expected_value="long"
                    )
        
        # R1-03: Single Asset Concentration Limit
        elif rule_id == "R1-03":
            max_single_asset_pct = params.get("max_single_asset_pct", 0.15)
            target_portion = decision.get("target_portion_of_balance", 0)
            
            if target_portion > max_single_asset_pct:
                return RuleViolation(
                    rule, severity,
                    f"Single asset exposure {target_portion:.2%} exceeds limit {max_single_asset_pct:.2%}",
                    actual_value=target_portion,
                    expected_value=f"<= {max_single_asset_pct}"
                )
        
        # R1-04: Minimum Cash Reserve
        elif rule_id == "R1-04":
            min_cash_pct = params.get("min_cash_pct", 0.10)
            cash = portfolio.get("cash", 0)
            total_assets = portfolio.get("total_assets", cash)
            
            if total_assets > 0:
                cash_pct = cash / total_assets
                if cash_pct < min_cash_pct:
                    return RuleViolation(
                        rule, severity,
                        f"Cash reserve {cash_pct:.2%} below minimum {min_cash_pct:.2%}",
                        actual_value=cash_pct,
                        expected_value=f">= {min_cash_pct}"
                    )
        
        # Add more rule checks as needed...
        
        return None
