"""
Rule Validator - Validates trading decisions against rules
"""
import logging
import sys
import os
from typing import Dict, Any, List, Tuple, Optional
from decimal import Decimal
from datetime import datetime, timedelta

from .rule_engine import RuleEngine, Rule, RuleLevel

# Import database models at module level to avoid dynamic import issues
# Try different import paths depending on execution context
get_db = None
AssetMetadata = None
Position = None
AIDecisionLog = None
Order = None
Trade = None

try:
    # First try relative import (when running from backend/)
    from database.connection import get_db
    from database.models import AssetMetadata, Position, AIDecisionLog, Order, Trade
except ImportError:
    try:
        # Fallback for absolute import (when running from project root)
        from backend.database.connection import get_db
        from backend.database.models import AssetMetadata, Position, AIDecisionLog, Order, Trade
    except ImportError:
        # If both fail, log warning
        pass

logger = logging.getLogger(__name__)

if not get_db:
    logger.warning("Failed to import database models for rule validation - R1-01 and R2-03 will skip database checks")


class RuleViolation:
    """Represents a rule violation"""
    
    def __init__(self, rule: Rule, severity: str, message: str, actual_value: Any = None, expected_value: Any = None, score: Optional[float] = None):
        self.rule = rule
        self.severity = severity  # "CRITICAL", "WARNING", "INFO"
        self.message = message
        self.actual_value = actual_value
        self.expected_value = expected_value
        self.score = score  # For R2 rules: continuous score 0-1 (1=perfect, 0=worst)
    
    def __repr__(self):
        score_str = f", score={self.score:.3f}" if self.score is not None else ""
        return f"RuleViolation({self.rule.id}, {self.severity}: {self.message}{score_str})"
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "rule_id": self.rule.id,
            "rule_name": self.rule.name,
            "severity": self.severity,
            "message": self.message,
            "actual_value": self.actual_value,
            "expected_value": self.expected_value
        }
        if self.score is not None:
            result["score"] = self.score
        return result


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
            # Check if database models are available
            if not get_db or not AssetMetadata:
                logger.warning("Database models not available for R1-01 validation")
                return None
            
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
            except Exception as e:
                # Gracefully handle database errors (e.g., missing tables in test environment)
                import logging
                logging.warning(f"R1-01: Could not query asset metadata for {symbol}: {e}")
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
        
        # R2-01: Decision Stability - Avoid ping-pong trades
        elif rule_id == "R2-01":
            # Check if database models are available
            if not get_db or not AIDecisionLog:
                logger.warning("Database models not available for R2-01 validation")
                return None
            
            reversal_window_minutes = params.get("reversal_window_minutes", 60)
            symbol = decision.get("symbol")
            operation = decision.get("operation", "").lower()
            account_id = portfolio.get("account_id")
            
            if not symbol or not account_id or operation not in ["open", "close"]:
                return None  # Can only check for actual trading operations
            
            # Query recent decisions for the same symbol
            db = next(get_db())
            try:
                cutoff_time = datetime.utcnow() - timedelta(minutes=reversal_window_minutes)
                recent_decisions = db.query(AIDecisionLog).filter(
                    AIDecisionLog.account_id == account_id,
                    AIDecisionLog.symbol == symbol,
                    AIDecisionLog.created_at >= cutoff_time,
                    AIDecisionLog.operation.in_(["open", "close"])
                ).order_by(AIDecisionLog.created_at.desc()).all()
                
                if recent_decisions:
                    latest = recent_decisions[0]
                    # Check for direction reversal (open->close or close->open)
                    if latest.operation != operation:
                        # Calculate time since reversal
                        time_diff = datetime.utcnow() - latest.created_at
                        interval_minutes = time_diff.total_seconds() / 60.0
                        
                        # Score: quadratic (smooth) function for gradual penalty
                        # 60 min = 1.0, 30 min = 0.25, 15 min = 0.0625, 0 min = 0.0
                        # This provides stronger penalty for very short intervals
                        ratio = min(1.0, interval_minutes / reversal_window_minutes)
                        score = ratio ** 2  # Quadratic smoothing for softer curve
                        
                        return RuleViolation(
                            rule, severity,
                            f"Direction reversal detected for {symbol} after {interval_minutes:.1f} minutes (target: >{reversal_window_minutes} min)",
                            actual_value=interval_minutes,
                            expected_value=f"> {reversal_window_minutes} minutes",
                            score=score
                        )
            except Exception as e:
                logger.warning(f"R2-01: Could not query decision history: {e}")
            finally:
                db.close()
        
        # R2-02: Dynamic Cash Utilization Efficiency (5%-15%)
        elif rule_id == "R2-02":
            target_min = params.get("target_cash_min", 0.05)
            target_max = params.get("target_cash_max", 0.15)
            
            cash = portfolio.get("cash", 0)
            total_equity = portfolio.get("total_equity") or portfolio.get("total_assets", cash)
            
            if total_equity > 0:
                cash_ratio = cash / total_equity
                
                # Check if outside target range
                if cash_ratio < target_min or cash_ratio > target_max:
                    # Calculate score based on deviation
                    if cash_ratio > target_max:
                        # Too much cash: score decays as cash increases
                        # 15%=1.0, 50%=0.58, 100%=0.0
                        deviation = cash_ratio - target_max
                        max_acceptable_deviation = 1.0 - target_max  # 0.85
                        score = max(0.0, 1.0 - deviation / max_acceptable_deviation)
                    else:
                        # Too little cash: score decays as cash decreases
                        # 5%=1.0, 2.5%=0.5, 0%=0.0
                        deviation = target_min - cash_ratio
                        max_acceptable_deviation = target_min  # 0.05
                        score = max(0.0, 1.0 - deviation / max_acceptable_deviation)
                    
                    direction = "above" if cash_ratio > target_max else "below"
                    return RuleViolation(
                        rule, severity,
                        f"Cash ratio {cash_ratio:.2%} is {direction} target range {target_min:.2%}-{target_max:.2%}",
                        actual_value=cash_ratio,
                        expected_value=f"{target_min:.2%}-{target_max:.2%}",
                        score=score
                    )
        
        # R2-03: Thematic Sector Affinity (30-50% in preferred sectors)
        elif rule_id == "R2-03":
            # Check if database models are available
            if not get_db or not AssetMetadata or not Position:
                logger.warning("Database models not available for R2-03 validation")
                return None
            
            preferred_sectors = params.get("preferred_sectors", [])
            preferred_crypto_themes = params.get("preferred_crypto_themes", [])
            target_min = params.get("target_allocation_min", 0.30)
            target_max = params.get("target_allocation_max", 0.50)
            
            # Get current positions to calculate sector allocation
            account_id = portfolio.get("account_id")
            if account_id:
                db = next(get_db())
                try:
                    positions = db.query(Position).filter(
                        Position.account_id == account_id,
                        Position.quantity > 0
                    ).all()
                    
                    total_value = 0
                    preferred_value = 0
                    
                    for pos in positions:
                        price = prices.get(pos.symbol, float(pos.avg_cost))
                        position_value = abs(float(pos.quantity) * price)
                        total_value += position_value
                        
                        # Check if position is in preferred sector
                        asset_meta = db.query(AssetMetadata).filter(
                            AssetMetadata.symbol == pos.symbol
                        ).first()
                        
                        if asset_meta and asset_meta.sector:
                            sector_normalized = asset_meta.sector.strip().lower()
                            # Use exact matching or word boundary matching
                            for pref in (preferred_sectors + preferred_crypto_themes):
                                pref_normalized = pref.strip().lower()
                                # Exact match or sector contains the preference as a whole word
                                if (sector_normalized == pref_normalized or 
                                    pref_normalized in sector_normalized.split() or
                                    sector_normalized in pref_normalized.split()):
                                    preferred_value += position_value
                                    break  # Don't double-count
                    
                    if total_value > 0:
                        preferred_ratio = preferred_value / total_value
                        
                        if preferred_ratio < target_min or preferred_ratio > target_max:
                            # Calculate continuous score
                            target_mid = (target_min + target_max) / 2.0  # 0.40
                            deviation = abs(preferred_ratio - target_mid)
                            # Max acceptable deviation: 0.40 (allows 0%-80% range)
                            max_deviation = 0.40
                            score = max(0.0, 1.0 - deviation / max_deviation)
                            
                            return RuleViolation(
                                rule, severity,
                                f"Preferred sector allocation {preferred_ratio:.2%} outside target range {target_min:.2%}-{target_max:.2%}",
                                actual_value=preferred_ratio,
                                expected_value=f"{target_min:.2%}-{target_max:.2%}",
                                score=score
                            )
                except Exception as e:
                    # Gracefully handle database errors
                    logger.warning(f"R2-03: Could not query positions/asset metadata: {e}")
                finally:
                    db.close()
        
        # R2-04: Position Scaling Smoothness
        elif rule_id == "R2-04":
            significant_threshold = params.get("significant_change_threshold", 0.10)
            max_single_move = params.get("max_single_move", 0.10)
            
            target_portion = decision.get("target_portion_of_balance", 0)
            symbol = decision.get("symbol")
            operation = decision.get("operation", "").lower()
            
            # Only check for actual position changes
            if operation not in ["open", "close"] or not symbol:
                return None
            
            # Check if position change exceeds threshold
            if target_portion > significant_threshold:
                # Calculate score: penalize large single moves
                # 10%=1.0, 50%=0.56, 100%=0.0
                excess = target_portion - significant_threshold
                max_excess = 1.0 - significant_threshold  # 0.90
                score = max(0.0, 1.0 - excess / max_excess)
                
                return RuleViolation(
                    rule, severity,
                    f"Position change {target_portion:.2%} exceeds smoothness threshold {significant_threshold:.2%} (prefer scaling across multiple decisions)",
                    actual_value=target_portion,
                    expected_value=f"<= {significant_threshold:.2%}",
                    score=score
                )
        
        # R2-05: Fee Sensitivity
        elif rule_id == "R2-05":
            min_profit_to_fee_ratio = params.get("min_profit_to_fee_ratio", 3.0)
            estimated_fee_rate = params.get("estimated_fee_rate", 0.001)
            estimated_slippage_rate = params.get("estimated_slippage_rate", 0.0005)
            
            symbol = decision.get("symbol")
            target_portion = decision.get("target_portion_of_balance", 0)
            operation = decision.get("operation", "").lower()
            
            if operation not in ["open", "close"] or not symbol:
                return None
            
            # Calculate estimated trade size
            total_equity = portfolio.get("total_equity") or portfolio.get("total_assets", 0)
            trade_value = total_equity * target_portion
            
            # Estimate total fees (commission + slippage)
            estimated_fees = trade_value * (estimated_fee_rate + estimated_slippage_rate)
            
            # For fee sensitivity, we use a heuristic:
            # Small trades are penalized. Score based on trade size as proxy for fee efficiency
            # Minimum viable trade: 1% of equity (assumed to be ~3x fees for typical holding period)
            min_viable_portion = 0.01
            
            if target_portion > 0 and target_portion < min_viable_portion:
                # Score: linear scale from 0 to min_viable_portion
                # 1%=1.0, 0.5%=0.5, 0%=0.0
                score = min(1.0, target_portion / min_viable_portion)
                
                return RuleViolation(
                    rule, severity,
                    f"Trade size {target_portion:.2%} may be too small relative to fees (estimated fees: ${estimated_fees:.2f})",
                    actual_value=target_portion,
                    expected_value=f">= {min_viable_portion:.2%}",
                    score=score
                )
        
        # Add more rule checks as needed...
        
        return None
