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
        
        # R0-03: Intraday Maximum Drawdown
        elif rule_id == "R0-03":
            max_drawdown_pct = params.get("max_drawdown_pct", 0.05)
            # Check if portfolio has previous day close equity for comparison
            prev_day_close_equity = portfolio.get("prev_day_close_equity")
            current_equity = portfolio.get("total_equity") or portfolio.get("total_assets", 0)
            
            if prev_day_close_equity and prev_day_close_equity > 0:
                drawdown = (prev_day_close_equity - current_equity) / prev_day_close_equity
                if drawdown > max_drawdown_pct:
                    return RuleViolation(
                        rule, severity,
                        f"Intraday drawdown {drawdown:.2%} exceeds maximum {max_drawdown_pct:.2%}",
                        actual_value=drawdown,
                        expected_value=f"<= {max_drawdown_pct}"
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
        
        # R1-01: Asset Blacklist (enhanced with sector and market cap checks)
        elif rule_id == "R1-01":
            from backend.database.connection import get_db
            from backend.database.models import AssetMetadata
            
            blacklist_symbols = params.get("blacklist_symbols", [])
            blacklist_sectors = params.get("blacklist_sectors", [])
            min_market_cap = params.get("min_market_cap_usd", 50000000)
            meme_filter = params.get("meme_coin_filter", True)
            
            symbol = decision.get("symbol")
            
            # Check symbol blacklist
            if symbol in blacklist_symbols:
                return RuleViolation(
                    rule, severity,
                    f"Symbol {symbol} is in blacklist",
                    actual_value=symbol,
                    expected_value="Not in blacklist"
                )
            
            # Query AssetMetadata for sector and market cap checks
            db = next(get_db())
            try:
                asset_meta = db.query(AssetMetadata).filter(AssetMetadata.symbol == symbol).first()
                
                if asset_meta:
                    # Check sector blacklist
                    if asset_meta.sector and asset_meta.sector.lower() in [s.lower() for s in blacklist_sectors]:
                        return RuleViolation(
                            rule, severity,
                            f"Symbol {symbol} belongs to blacklisted sector: {asset_meta.sector}",
                            actual_value=asset_meta.sector,
                            expected_value="Not in blacklist sectors"
                        )
                    
                    # Check meme coin filter
                    if meme_filter and asset_meta.is_meme == "true":
                        return RuleViolation(
                            rule, severity,
                            f"Symbol {symbol} is a meme coin (prohibited)",
                            actual_value="meme_coin",
                            expected_value="Not a meme coin"
                        )
                    
                    # Check minimum market cap
                    if asset_meta.market_cap_usd is not None and asset_meta.market_cap_usd < min_market_cap:
                        return RuleViolation(
                            rule, severity,
                            f"Symbol {symbol} market cap ${asset_meta.market_cap_usd:,.0f} below minimum ${min_market_cap:,.0f}",
                            actual_value=float(asset_meta.market_cap_usd),
                            expected_value=f">= ${min_market_cap:,.0f}"
                        )
            finally:
                db.close()
        
        # R1-02: Single Asset Concentration Limit (formerly R1-03)
        elif rule_id == "R1-02":
            max_single_asset_pct = params.get("max_single_asset_pct", 0.15)
            target_portion = decision.get("target_portion_of_balance", 0)
            
            if target_portion > max_single_asset_pct:
                return RuleViolation(
                    rule, severity,
                    f"Single asset exposure {target_portion:.2%} exceeds limit {max_single_asset_pct:.2%}",
                    actual_value=target_portion,
                    expected_value=f"<= {max_single_asset_pct}"
                )
        
        # R1-03: Minimum Cash Reserve (formerly R1-04)
        elif rule_id == "R1-03":
            min_cash_pct = params.get("min_cash_pct", 0.10)
            cash = portfolio.get("cash", 0)
            # Use total_equity if available, otherwise fall back to total_assets
            total_equity = portfolio.get("total_equity") or portfolio.get("total_assets", cash)
            
            if total_equity > 0:
                cash_pct = cash / total_equity
                if cash_pct < min_cash_pct:
                    return RuleViolation(
                        rule, severity,
                        f"Cash reserve {cash_pct:.2%} below minimum {min_cash_pct:.2%}",
                        actual_value=cash_pct,
                        expected_value=f">= {min_cash_pct}"
                    )

        
        # Add more rule checks as needed...
        
        return None
