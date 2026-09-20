"""
Rule Validator - Validates trading decisions against rules
"""
import json
import logging
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime, timedelta, timezone

from .rule_engine import RuleEngine, Rule, RuleLevel

# Import centralized crypto sector classification
try:
    from config.asset_config import CRYPTO_SECTOR_MAP
except ImportError:
    # Fallback for absolute import
    from backend.config.asset_config import CRYPTO_SECTOR_MAP

# Import database models at module level to avoid dynamic import issues
# Try different import paths depending on execution context
get_db = None
AssetMetadata = None
Position = None
AIDecisionLog = None
RuntimeEvent = None
Order = None
Trade = None

try:
    # First try relative import (when running from backend/)
    from database.connection import get_db
    from database.models import AssetMetadata, Position, AIDecisionLog, Account, AccountSnapshot, RuntimeEvent
except ImportError:
    try:
        # Fallback for absolute import (when running from project root)
        from backend.database.connection import get_db
        from backend.database.models import AssetMetadata, Position, AIDecisionLog, Account, AccountSnapshot, RuntimeEvent
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

    def _check_post_execution_state(self, rule, decision, portfolio, severity):
        """Check one committed portfolio; filled actions must not be added again."""
        positions = portfolio["positions"]
        equity = portfolio["total_equity"]
        notionals = {}
        for position in positions:
            symbol = position["symbol"]
            value = abs(position["quantity"] * position["valuation_price"])
            notionals[symbol] = notionals.get(symbol, 0.0) + value
        params = rule.parameters
        if rule.id == "R0-01":
            limit = params.get("max_leverage", 5)
            leverage = decision.get("leverage", 1)
            if leverage > limit:
                return RuleViolation(
                    rule,
                    severity,
                    f"Order leverage {leverage}x exceeds maximum {limit}x",
                    leverage,
                    f"<= {limit}",
                )
            exposure = sum(notionals.values())
            if equity > 0 and exposure / equity > limit:
                return RuleViolation(
                    rule,
                    severity,
                    f"Portfolio leverage {exposure / equity:.2f}x exceeds maximum {limit}x",
                    exposure / equity,
                    f"<= {limit}",
                )
        elif rule.id == "R0-02":
            margin = portfolio["margin_used"]
            limit = params.get("min_margin_level", 0.10)
            if margin > 0 and equity / margin < limit:
                return RuleViolation(
                    rule,
                    severity,
                    f"Margin level {equity / margin:.2%} below minimum {limit:.2%}",
                    equity / margin,
                    f">= {limit}",
                )
        elif rule.id == "R1-02":
            limit = params.get("max_single_asset_pct", 0.15)
            if equity > 0 and notionals:
                symbol, value = max(notionals.items(), key=lambda item: item[1])
                ratio = value / equity
                if ratio > limit:
                    return RuleViolation(
                        rule,
                        severity,
                        f"Post-execution {symbol} exposure {ratio:.2%} exceeds limit {limit:.2%}",
                        ratio,
                        f"<= {limit}",
                    )
        elif rule.id == "R2-03":
            preferred = {
                item.lower()
                for item in params.get("preferred_sectors", [])
                + params.get("preferred_crypto_themes", [])
            }
            preferred_value = sum(
                value
                for symbol, value in notionals.items()
                if CRYPTO_SECTOR_MAP.get(symbol, "").lower() in preferred
            )
            total = sum(notionals.values())
            ratio = preferred_value / total if total > 0 else 0.0
            lower = params.get("target_allocation_min", 0.30)
            upper = params.get("target_allocation_max", 0.70)
            if ratio < lower or ratio > upper:
                score = max(0.0, 1.0 - abs(ratio - (lower + upper) / 2.0) / 0.40)
                return RuleViolation(
                    rule,
                    severity,
                    f"Preferred sector allocation {ratio:.2%} of position notional outside target range {lower:.2%}-{upper:.2%}",
                    ratio,
                    f"{lower:.2%}-{upper:.2%}",
                    score=score,
                )
        return None

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
                if portfolio.get("audit_phase") == "post_execution":
                    raise RuntimeError(f"Compliance check unavailable: {rule.id}") from e
        
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
                if portfolio.get("audit_phase") == "post_execution":
                    raise RuntimeError(f"Compliance check unavailable: {rule.id}") from e
        
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
                if portfolio.get("audit_phase") == "post_execution":
                    raise RuntimeError(f"Compliance check unavailable: {rule.id}") from e
        
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
        if portfolio.get("audit_phase") == "post_execution" and rule_id in {"R0-01", "R0-02", "R1-02", "R2-03"}:
            return self._check_post_execution_state(rule, decision, portfolio, severity)
        
        # R0-01: Maximum Leverage Limit
        # Checks both (a) the individual order's leverage and (b) the resulting
        # portfolio-level leverage = total_notional / total_equity.
        if rule_id == "R0-01":
            max_leverage = params.get("max_leverage", 5)

            # (a) Per-order leverage check
            decision_leverage = decision.get("leverage", 1)
            if decision_leverage > max_leverage:
                return RuleViolation(
                    rule, severity,
                    f"Order leverage {decision_leverage}x exceeds maximum {max_leverage}x",
                    actual_value=decision_leverage,
                    expected_value=f"<= {max_leverage}"
                )

            # (b) Portfolio-level leverage check: total_notional / total_equity
            account_id = portfolio.get("account_id")
            if account_id and get_db and Position:
                db = next(get_db())
                try:
                    positions = db.query(Position).filter(
                        Position.account_id == account_id,
                        Position.quantity > 0
                    ).all()
                    total_notional = 0.0
                    for pos in positions:
                        price = prices.get(pos.symbol, float(pos.avg_cost))
                        lev = float(pos.leverage) if pos.leverage and pos.leverage > 0 else 1.0
                        total_notional += abs(float(pos.quantity)) * price * lev
                    total_equity = portfolio.get("total_assets") or portfolio.get("total_equity", 0)
                    if total_equity > 0 and total_notional > 0:
                        portfolio_leverage = total_notional / total_equity
                        if portfolio_leverage > max_leverage:
                            return RuleViolation(
                                rule, severity,
                                f"Portfolio leverage {portfolio_leverage:.2f}x exceeds maximum {max_leverage}x "
                                f"(total_notional=${total_notional:,.0f}, equity=${total_equity:,.0f})",
                                actual_value=round(portfolio_leverage, 2),
                                expected_value=f"<= {max_leverage}"
                            )
                except Exception as e:
                    logger.warning(f"R0-01: Could not compute portfolio leverage: {e}")
                finally:
                    db.close()
        
        # R0-02: Maintenance Margin
        # margin_level = total_equity / margin_used
        # Triggered when accumulated losses shrink equity close to the locked margin.
        elif rule_id == "R0-02":
            min_margin_level = params.get("min_margin_level", 0.10)
            account_id = portfolio.get("account_id")
            if account_id and get_db and Account:
                db = next(get_db())
                try:
                    account = db.query(Account).filter(Account.id == account_id).first()
                    if account:
                        margin_used = float(account.margin_used or 0)
                        if margin_used > 0:
                            total_equity = portfolio.get("total_assets") or portfolio.get("total_equity", 0)
                            margin_level = total_equity / margin_used
                            if margin_level < min_margin_level:
                                return RuleViolation(
                                    rule, severity,
                                    f"Margin level {margin_level:.2%} below minimum {min_margin_level:.2%} "
                                    f"(equity=${total_equity:,.0f}, margin_used=${margin_used:,.0f})",
                                    actual_value=round(margin_level, 4),
                                    expected_value=f">= {min_margin_level}"
                                )
                except Exception as e:
                    logger.warning(f"R0-02: Could not query account margin data: {e}")
                finally:
                    db.close()
        
        # R0-03: Intraday Maximum Drawdown
        # Uses the most recent AccountSnapshot from before today UTC as the baseline.
        elif rule_id == "R0-03":
            max_drawdown_pct = params.get("max_drawdown_pct", 0.05)
            account_id = portfolio.get("account_id")
            if account_id and get_db and AccountSnapshot:
                db = next(get_db())
                try:
                    today_start = datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
                    prev_snapshot = db.query(AccountSnapshot).filter(
                        AccountSnapshot.account_id == account_id,
                        AccountSnapshot.ts < today_start
                    ).order_by(AccountSnapshot.ts.desc()).first()

                    if prev_snapshot:
                        prev_day_close_equity = float(prev_snapshot.total_equity)
                        current_equity = portfolio.get("total_assets") or portfolio.get("total_equity", 0)
                        if prev_day_close_equity > 0 and current_equity > 0:
                            drawdown = (prev_day_close_equity - current_equity) / prev_day_close_equity
                            if drawdown > max_drawdown_pct:
                                return RuleViolation(
                                    rule, severity,
                                    f"Intraday drawdown {drawdown:.2%} exceeds maximum {max_drawdown_pct:.2%} "
                                    f"(prev_close=${prev_day_close_equity:,.0f}, current=${current_equity:,.0f})",
                                    actual_value=round(drawdown, 4),
                                    expected_value=f"<= {max_drawdown_pct}"
                                )
                except Exception as e:
                    logger.warning(f"R0-03: Could not query account snapshots: {e}")
                    if portfolio.get("audit_phase") == "post_execution":
                        raise
                finally:
                    db.close()
        
        # R0-04: Maximum Single Order Notional Value
        elif rule_id == "R0-04":
            max_order_pct = params.get("max_order_pct", 0.20)
            operation = decision.get("operation", "").lower()
            size_mode = decision.get("size_mode", "portion")
            total_equity = portfolio.get("total_assets") or portfolio.get("total_equity", 0)

            if operation == "all_in":
                # all_in deploys all available cash
                cash = portfolio.get("cash", 0)
                effective_portion = cash / total_equity if total_equity > 0 else 1.0
            elif size_mode == "usd":
                # USD-sized order: convert to portfolio fraction
                usd_amount = decision.get("usd_amount", 0)
                effective_portion = usd_amount / total_equity if total_equity > 0 else 0
            else:
                effective_portion = decision.get("target_portion_of_balance", 0)

            if effective_portion > max_order_pct:
                return RuleViolation(
                    rule, severity,
                    f"Order size {effective_portion:.2%} exceeds maximum {max_order_pct:.2%} of portfolio",
                    actual_value=round(effective_portion, 4),
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
                if portfolio.get("audit_phase") == "post_execution":
                    raise
            finally:
                db.close()
        
        # R1-02: Single Asset Concentration Limit (formerly R1-03)
        # Checks combined exposure = (existing position value + new order value) / total_equity.
        elif rule_id == "R1-02":
            max_single_asset_pct = params.get("max_single_asset_pct", 0.15)
            operation = decision.get("operation", "").lower()
            symbol = decision.get("symbol")
            target_portion = decision.get("target_portion_of_balance", 0)
            account_id = portfolio.get("account_id")
            total_equity = portfolio.get("total_assets") or portfolio.get("total_equity", 0)

            # Only concentration risk for open orders; closing reduces exposure
            if operation == "open" and symbol and account_id and get_db and Position and total_equity > 0:
                db = next(get_db())
                try:
                    existing = db.query(Position).filter(
                        Position.account_id == account_id,
                        Position.symbol == symbol,
                        Position.quantity > 0
                    ).first()
                    existing_exposure = 0.0
                    if existing:
                        price = prices.get(symbol, float(existing.avg_cost))
                        existing_exposure = abs(float(existing.quantity)) * price
                    new_order_value = total_equity * target_portion
                    combined_pct = (existing_exposure + new_order_value) / total_equity
                    if combined_pct > max_single_asset_pct:
                        return RuleViolation(
                            rule, severity,
                            f"Combined {symbol} exposure {combined_pct:.2%} exceeds limit {max_single_asset_pct:.2%} "
                            f"(existing=${existing_exposure:,.0f} + new=${new_order_value:,.0f})",
                            actual_value=round(combined_pct, 4),
                            expected_value=f"<= {max_single_asset_pct}"
                        )
                except Exception as e:
                    logger.warning(f"R1-02: Could not query existing positions: {e}")
                    # Fallback to simple per-order check
                    if target_portion > max_single_asset_pct:
                        return RuleViolation(
                            rule, severity,
                            f"Single asset order {target_portion:.2%} exceeds limit {max_single_asset_pct:.2%}",
                            actual_value=round(target_portion, 4),
                            expected_value=f"<= {max_single_asset_pct}"
                        )
                finally:
                    db.close()
            elif operation not in ["open"] and target_portion > max_single_asset_pct:
                # Fallback for non-open operations or missing DB
                return RuleViolation(
                    rule, severity,
                    f"Single asset order {target_portion:.2%} exceeds limit {max_single_asset_pct:.2%}",
                    actual_value=round(target_portion, 4),
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
                recent_query = db.query(AIDecisionLog).filter(
                    AIDecisionLog.account_id == account_id,
                    AIDecisionLog.symbol == symbol,
                    AIDecisionLog.created_at >= cutoff_time,
                    AIDecisionLog.operation.in_(["open", "close"])
                )
                decision_time = datetime.utcnow()
                if portfolio.get("audit_phase") == "post_execution" and decision.get("order_id"):
                    current_log = db.query(AIDecisionLog).filter(
                        AIDecisionLog.account_id == account_id,
                        AIDecisionLog.order_id == decision["order_id"],
                    ).order_by(AIDecisionLog.id.asc()).first()
                    if current_log is not None:
                        # The audit runs after all tools commit. Check the history
                        # before this fill, excluding its own and later tool logs.
                        recent_query = recent_query.filter(AIDecisionLog.id < current_log.id)
                        decision_time = current_log.created_at
                recent_decisions = recent_query.order_by(
                    AIDecisionLog.created_at.desc(), AIDecisionLog.id.desc()
                ).all()
                
                if recent_decisions:
                    latest = recent_decisions[0]
                    # Check for direction reversal (open->close or close->open)
                    if latest.operation != operation:
                        # Calculate time since reversal
                        time_diff = decision_time - latest.created_at
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
                if portfolio.get("audit_phase") == "post_execution":
                    raise
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
                    # Calculate score based on deviation using quadratic penalty
                    if cash_ratio > target_max:
                        # Too much cash: quadratic penalty for over-allocation
                        # Normalized deviation (100% cash = 1.0 deviation)
                        # Examples:
                        # - 20%: 1 - (0.05/0.85)^2 = 0.997 (very light)
                        # - 30%: 1 - (0.15/0.85)^2 = 0.969 (light)
                        # - 50%: 1 - (0.35/0.85)^2 = 0.831 (moderate)
                        # - 100%: 1 - (0.85/0.85)^2 = 0.000 (worst)
                        deviation = cash_ratio - target_max
                        max_acceptable_deviation = 1.0 - target_max  # 0.85
                        score = max(0.0, 1.0 - (deviation / max_acceptable_deviation) ** 2)
                    else:
                        # Too little cash: quadratic penalty for under-allocation
                        # Examples:
                        # - 4%: 1 - (0.01/0.05)^2 = 0.960 (light)
                        # - 2.5%: 1 - (0.025/0.05)^2 = 0.750 (moderate)
                        # - 0%: 1 - (0.05/0.05)^2 = 0.000 (worst)
                        deviation = target_min - cash_ratio
                        max_acceptable_deviation = target_min  # 0.05
                        score = max(0.0, 1.0 - (deviation / max_acceptable_deviation) ** 2)
                    
                    direction = "above" if cash_ratio > target_max else "below"
                    return RuleViolation(
                        rule, severity,
                        f"Cash ratio {cash_ratio:.2%} is {direction} target range {target_min:.2%}-{target_max:.2%}",
                        actual_value=cash_ratio,
                        expected_value=f"{target_min:.2%}-{target_max:.2%}",
                        score=score
                    )
        
        # R2-03: Thematic Sector Affinity (40-60% in preferred sectors)
        elif rule_id == "R2-03":
            # Check if database models are available
            if not get_db or not Position:
                logger.warning("Database models not available for R2-03 validation")
                return None
            
            preferred_sectors = params.get("preferred_sectors", [])
            preferred_crypto_themes = params.get("preferred_crypto_themes", [])
            target_min = params.get("target_allocation_min", 0.30)
            target_max = params.get("target_allocation_max", 0.70)
            
            # logger.info(f"R2-03 [rule_validator]: preferred_sectors={preferred_sectors}, preferred_crypto_themes={preferred_crypto_themes}")
            # logger.info(f"R2-03 [rule_validator]: target range={target_min:.0%}-{target_max:.0%}")
            
            # Combine all preferred themes
            all_preferred = set(s.lower() for s in (preferred_sectors + preferred_crypto_themes))
            # logger.info(f"R2-03 [rule_validator]: all_preferred (lowercase)={all_preferred}")
            
            # Get current positions to calculate sector allocation
            account_id = portfolio.get("account_id")
            if account_id:
                db = next(get_db())
                try:
                    positions = db.query(Position).filter(
                        Position.account_id == account_id,
                        Position.quantity > 0
                    ).all()
                    
                    # logger.info(f"R2-03 [rule_validator]: Found {len(positions)} positions for account_id={account_id}")
                    
                    total_value = 0
                    preferred_value = 0
                    
                    for pos in positions:
                        price = prices.get(pos.symbol, float(pos.avg_cost))
                        position_value = abs(float(pos.quantity) * price)
                        total_value += position_value
                        
                        # Use built-in sector mapping (based on crypto_prices and positions)
                        sector = CRYPTO_SECTOR_MAP.get(pos.symbol)
                        sector_lower = sector.lower() if sector else None
                        # logger.info(f"R2-03 [rule_validator]: {pos.symbol} -> sector={sector}, sector_lower={sector_lower}, value={position_value:.2f}")
                        
                        if sector and sector_lower in all_preferred:
                            preferred_value += position_value
                            # logger.info(f"R2-03 [rule_validator]: ✓ {pos.symbol} MATCHED (sector={sector})")
                        else:
                            # logger.info(f"R2-03 [rule_validator]: ✗ {pos.symbol} NOT matched (sector={sector}, need one of {all_preferred})")
                            pass
                    
                    # Calculate preferred ratio (0 if no positions)
                    preferred_ratio = preferred_value / total_value if total_value > 0 else 0.0
                    # logger.info(f"R2-03 [rule_validator]: SUMMARY - total={total_value:.2f}, preferred={preferred_value:.2f}, ratio={preferred_ratio:.2%}")
                    
                    # Check if outside target range (including 0% case)
                    if preferred_ratio < target_min or preferred_ratio > target_max:
                        # Calculate continuous score
                        target_mid = (target_min + target_max) / 2.0
                        deviation = abs(preferred_ratio - target_mid)
                        # Max acceptable deviation based on target range width
                        # For 30-70% target, mid=50%, max_deviation=40% allows [10%-90%] positive scores
                        max_deviation = 0.40
                        score = max(0.0, 1.0 - deviation / max_deviation)
                        
                        # logger.info(f"R2-03 [rule_validator]: VIOLATION - ratio {preferred_ratio:.2%} outside [{target_min:.0%}-{target_max:.0%}], score={score:.3f}")
                        
                        return RuleViolation(
                            rule, severity,
                            f"Preferred sector allocation {preferred_ratio:.2%} outside target range {target_min:.2%}-{target_max:.2%}",
                            actual_value=preferred_ratio,
                            expected_value=f"{target_min:.2%}-{target_max:.2%}",
                            score=score
                        )
                    # else:
                    #     logger.info(f"R2-03 [rule_validator]: PASS - ratio {preferred_ratio:.2%} within [{target_min:.0%}-{target_max:.0%}]")
                except Exception as e:
                    # Gracefully handle database errors
                    logger.warning(f"R2-03: Could not query positions/asset metadata: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    db.close()
        
        # R2-04: Position Scaling Smoothness
        elif rule_id == "R2-04":
            significant_threshold = params.get("significant_change_threshold", 0.10)
            
            target_portion = decision.get("target_portion_of_balance", 0)
            symbol = decision.get("symbol")
            operation = decision.get("operation", "").lower()
            
            # Only check for actual position changes
            if operation not in ["open", "close"] or not symbol:
                return None
            
            # Check if position change exceeds threshold
            if target_portion > significant_threshold:
                # Calculate score: quadratic penalty for large single moves
                # Normalized deviation (100% move = 1.0 deviation)
                # Examples:
                # - 15%: 1 - (0.05/0.9)^2 = 0.997 (very light)
                # - 30%: 1 - (0.2/0.9)^2 = 0.951 (light)
                # - 50%: 1 - (0.4/0.9)^2 = 0.802 (moderate)
                # - 100%: 1 - (0.9/0.9)^2 = 0.000 (worst)
                excess = target_portion - significant_threshold
                max_excess = 1.0 - significant_threshold  # 0.90
                score = max(0.0, 1.0 - (excess / max_excess) ** 2)
                
                return RuleViolation(
                    rule, severity,
                    f"Position change {target_portion:.2%} exceeds smoothness threshold {significant_threshold:.2%} (prefer scaling across multiple decisions)",
                    actual_value=target_portion,
                    expected_value=f"<= {significant_threshold:.2%}",
                    score=score
                )
        
        # R2-05: Fee Sensitivity
        elif rule_id == "R2-05":
            min_trade_size_ratio = params.get("min_trade_size_ratio", 0.10)  # Default 10%
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
            # Minimum viable trade: configurable (default 10% of equity)
            
            if target_portion > 0 and target_portion < min_trade_size_ratio:
                # Quadratic penalty (non-linear) for consistency with other R2 rules
                # Normalized deviation: 0% = 1.0 deviation, 10% = 0.0 deviation
                # Formula: score = 1 - ((min - actual) / min)^2
                # Examples:
                # - 10%: 1 - 0^2 = 1.000 (no penalty)
                # - 7.5%: 1 - (0.25)^2 = 0.938 (light penalty)
                # - 5%: 1 - (0.5)^2 = 0.750 (moderate penalty)
                # - 2.5%: 1 - (0.75)^2 = 0.438 (heavy penalty)
                # - 0%: 1 - (1.0)^2 = 0.000 (worst)
                deviation = (min_trade_size_ratio - target_portion) / min_trade_size_ratio
                score = max(0.0, 1 - deviation ** 2)
                
                return RuleViolation(
                    rule, severity,
                    f"Trade size {target_portion:.2%} below recommended minimum {min_trade_size_ratio:.2%} (estimated fees: ${estimated_fees:.2f})",
                    actual_value=target_portion,
                    expected_value=f">= {min_trade_size_ratio:.2%}",
                    score=score
                )
        
        # R2-06: Active Engagement - Penalize consecutive HOLD actions
        elif rule_id == "R2-06":
            # Check if database models are available
            if not get_db or not AIDecisionLog:
                logger.warning("Database models not available for R2-06 validation")
                return None
            
            max_consecutive_before_penalty = params.get("max_consecutive_holds_before_penalty", 1)
            penalty_max_reference = params.get("hold_penalty_max_reference", 3)
            
            operation = decision.get("operation", "").lower()
            account_id = portfolio.get("account_id")
            
            # Only check when the decision is HOLD
            if operation != "hold" or not account_id:
                return None
            
            # Query recent decisions to count consecutive HOLDs
            db = next(get_db())
            try:
                consecutive_holds = 1  # Current decision is HOLD.
                if portfolio.get("audit_phase") == "post_execution":
                    # Tool logs and the display summary are observations of one
                    # round. Only completed runtime results identify its outcome.
                    rounds = db.query(RuntimeEvent).filter(
                        RuntimeEvent.account_id == account_id,
                        RuntimeEvent.event_type == "run.result",
                    ).order_by(RuntimeEvent.created_at.desc(), RuntimeEvent.sequence.desc()).yield_per(50)
                    seen_rounds = {portfolio.get("decision_round_id")}
                    for event in rounds:
                        if event.decision_round_id in seen_rounds:
                            continue
                        seen_rounds.add(event.decision_round_id)
                        result = json.loads(event.payload)
                        # A fill breaks the streak even if the agent subsequently
                        # reached its step limit or reported HOLD.
                        if any(
                            trade.get("executed") is True
                            and trade.get("operation") in {"open", "close", "all_in", "close_all"}
                            for trade in result.get("executed_trades", [])
                        ):
                            break
                        if result.get("termination_reason") != "hold":
                            break
                        consecutive_holds += 1
                else:
                    recent_decisions = db.query(AIDecisionLog).filter(
                        AIDecisionLog.account_id == account_id,
                        AIDecisionLog.operation != "summary",
                    ).order_by(AIDecisionLog.decision_time.desc()).limit(20).all()
                    for past_decision in recent_decisions:
                        past_op = (past_decision.operation or "").lower()
                        if past_op not in {"hold", ""}:
                            break
                        consecutive_holds += 1
                
                # Check if we should apply penalty
                if consecutive_holds > max_consecutive_before_penalty:
                    # Calculate score with quadratic penalty
                    # Formula: score = max(0, 1 - ((n - 1) / penalty_max_ref)^2)
                    # Examples (penalty_max_ref=3):
                    # - n=2: 0.889 (-11%)
                    # - n=3: 0.556 (-44%)
                    # - n=4: 0.000 (-100%)
                    deviation = (consecutive_holds - 1) / penalty_max_reference
                    score = max(0.0, 1 - deviation ** 2)
                    
                    return RuleViolation(
                        rule, severity,
                        f"Consecutive HOLD count {consecutive_holds} exceeds threshold (max before penalty: {max_consecutive_before_penalty}). Consider active trading unless justified by low volatility or hard constraints.",
                        actual_value=consecutive_holds,
                        expected_value=f"<= {max_consecutive_before_penalty}",
                        score=score
                    )
            except Exception as e:
                logger.warning(f"R2-06: Could not query decision history: {e}")
                if portfolio.get("audit_phase") == "post_execution":
                    raise
            finally:
                db.close()
        
        # Add more rule checks as needed...
        
        return None
