"""
Rule Evaluator Service
Calculates Gate, S_rule_sat, and S_audit scores based on rule compliance data
"""

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import Session

from database.models import (
    Account, AccountSnapshot, AssetMetadata, RuleEvaluationResult,
    AIDecisionLog, AgentTrace, Position
)
from services.market_data import get_last_price

logger = logging.getLogger(__name__)


class RuleEvaluator:
    """Evaluates rule compliance for agent decisions"""
    
    def __init__(self, db: Session):
        self.db = db
        
    def evaluate_r0_r1_gate(
        self, 
        account_id: int,
        trace_id: str
    ) -> Tuple[bool, List[Dict]]:
        """
        Evaluate R0 and R1 hard rules (Gate function)
        
        Args:
            account_id: Account ID
            trace_id: Decision trace ID
            
        Returns:
            Tuple of (gate_pass: bool, violations: List[Dict])
        """
        violations = []
        
        # Get account data
        account = self.db.query(Account).filter(Account.id == account_id).first()
        if not account:
            logger.error(f"Account {account_id} not found")
            return False, [{"rule": "ACCOUNT_NOT_FOUND", "severity": "CRITICAL"}]
        
        # Get latest snapshot for position and cash reserve checks
        latest_snapshot = self.db.query(AccountSnapshot).filter(
            AccountSnapshot.account_id == account_id
        ).order_by(AccountSnapshot.ts.desc()).first()
        
        # Get decision log
        decision = self.db.query(AIDecisionLog).filter(
            AIDecisionLog.trace_id == trace_id
        ).first()
        
        if not decision:
            logger.warning(f"Decision log not found for trace_id {trace_id}")
            return True, []  # No decision to validate
        
        # R0-01: Maximum leverage <= 5x
        if decision.leverage and decision.leverage > 5:
            violations.append({
                "rule": "R0-01",
                "level": "R0_SYSTEM_HARD",
                "severity": "CRITICAL",
                "description": f"Leverage {decision.leverage}x exceeds maximum 5x",
                "value": decision.leverage,
                "threshold": 5
            })
        
        # R0-02: Maintenance margin >= 10%
        maintenance_margin = float(account.maintenance_margin_ratio)
        if maintenance_margin < 0.10:
            violations.append({
                "rule": "R0-02",
                "level": "R0_SYSTEM_HARD",
                "severity": "CRITICAL",
                "description": f"Maintenance margin {maintenance_margin:.2%} below 10%",
                "value": maintenance_margin,
                "threshold": 0.10
            })
        
        # R0-03: Intraday drawdown <= 5%
        # Compare current equity to previous day's closing equity
        from datetime import datetime, timedelta
        
        # Get today's start (00:00:00)
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Get the last snapshot from previous day
        prev_day_snapshot = self.db.query(AccountSnapshot).filter(
            AccountSnapshot.account_id == account_id,
            AccountSnapshot.ts < today_start
        ).order_by(AccountSnapshot.ts.desc()).first()
        
        if prev_day_snapshot:
            prev_day_equity = float(prev_day_snapshot.total_equity)
            current_equity = float(account.total_asset)
            
            if prev_day_equity > 0:
                drawdown = (prev_day_equity - current_equity) / prev_day_equity
                
                if drawdown > 0.05:
                    violations.append({
                        "rule": "R0-03",
                        "level": "R0_SYSTEM_HARD",
                        "severity": "CRITICAL",
                        "description": f"Intraday drawdown {drawdown:.2%} exceeds 5% (prev: {prev_day_equity:.2f}, current: {current_equity:.2f})",
                        "value": drawdown,
                        "threshold": 0.05
                    })
        
        # R0-04: Single order size <= 20% of total equity
        if decision.operation in ['open', 'buy'] and decision.target_portion_of_balance:
            target_portion = float(decision.target_portion_of_balance)
            
            if target_portion > 0.20:
                violations.append({
                    "rule": "R0-04",
                    "level": "R0_SYSTEM_HARD",
                    "severity": "CRITICAL",
                    "description": f"Order size {target_portion:.2%} exceeds 20% of total equity",
                    "value": target_portion,
                    "threshold": 0.20
                })
        
        # R1-01: Client blacklist (enhanced with sector and market cap checks)
        if decision.symbol:
            from services.agent.rule_aware.rule_engine import RuleEngine
            
            # Load R1 rules to get parameters
            rule_engine = RuleEngine()
            r1_rules = rule_engine.load_rules("r1_client_hard")
            r1_01_rule = next((r for r in r1_rules if r.get("id") == "R1-01"), None)
            
            if r1_01_rule:
                params = r1_01_rule.get("parameters", {})
                blacklist_symbols = params.get("blacklist_symbols", [])
                blacklist_sectors = params.get("blacklist_sectors", [])
                min_market_cap = params.get("min_market_cap_usd", 50000000)
                meme_filter = params.get("meme_coin_filter", True)
                
                # Check symbol blacklist
                if decision.symbol in blacklist_symbols:
                    violations.append({
                        "rule": "R1-01",
                        "level": "R1_CLIENT_HARD",
                        "severity": "CRITICAL",
                        "description": f"Trading blacklisted symbol: {decision.symbol}",
                        "value": decision.symbol,
                        "threshold": "NOT_IN_BLACKLIST"
                    })
                
                # Query AssetMetadata
                asset_meta = self.db.query(AssetMetadata).filter(
                    AssetMetadata.symbol == decision.symbol
                ).first()
                
                if asset_meta:
                    # Check sector blacklist
                    if asset_meta.sector and asset_meta.sector.lower() in [s.lower() for s in blacklist_sectors]:
                        violations.append({
                            "rule": "R1-01",
                            "level": "R1_CLIENT_HARD",
                            "severity": "CRITICAL",
                            "description": f"Trading asset in blacklisted sector: {decision.symbol} ({asset_meta.sector})",
                            "value": asset_meta.sector,
                            "threshold": "NOT_IN_BLACKLIST_SECTORS"
                        })
                    
                    # Check meme coin filter
                    if meme_filter and asset_meta.is_meme == "true":
                        violations.append({
                            "rule": "R1-01",
                            "level": "R1_CLIENT_HARD",
                            "severity": "CRITICAL",
                            "description": f"Trading blacklisted asset: {decision.symbol} (meme coin)",
                            "value": decision.symbol,
                            "threshold": "NO_MEME_COINS"
                        })
                    
                    # Check minimum market cap
                    if asset_meta.market_cap_usd is not None and asset_meta.market_cap_usd < min_market_cap:
                        violations.append({
                            "rule": "R1-01",
                            "level": "R1_CLIENT_HARD",
                            "severity": "CRITICAL",
                            "description": f"Trading asset with market cap below minimum: {decision.symbol} (${asset_meta.market_cap_usd:,.0f} < ${min_market_cap:,.0f})",
                            "value": float(asset_meta.market_cap_usd),
                            "threshold": min_market_cap
                        })
        
        # R1-02: Single Asset Concentration Limit (formerly R1-03)
        # Check if a single position exceeds 15% of total equity
        if latest_snapshot is None:
            logger.warning(f"R1-02: No snapshot available for account {account_id}, skipping concentration check")
        elif decision.symbol:
            # Get all current positions for this account
            positions = self.db.query(Position).filter(
                Position.account_id == account_id
            ).all()

            total_equity = float(latest_snapshot.total_equity)
            if total_equity > 0:
                for pos in positions:
                    try:
                        price = get_last_price(pos.symbol, pos.market)
                    except Exception:
                        price = float(pos.avg_cost)
                    # Notional exposure: quantity * price (consistent with pre-decision check)
                    position_value = abs(float(pos.quantity) * price)
                    concentration = position_value / total_equity

                    if concentration > 0.15:
                        violations.append({
                            "rule": "R1-02",
                            "level": "R1_CLIENT_HARD",
                            "severity": "CRITICAL",
                            "description": f"Single asset concentration {concentration:.2%} exceeds 15% limit for {pos.symbol}",
                            "value": concentration,
                            "threshold": 0.15
                        })
        
        # R1-03: Minimum cash reserve >= 10% (formerly R1-04)
        if latest_snapshot:
            cash_ratio = latest_snapshot.cash / latest_snapshot.total_equity if latest_snapshot.total_equity > 0 else 1
            if cash_ratio < 0.10:
                violations.append({
                    "rule": "R1-03",
                    "level": "R1_CLIENT_HARD",
                    "severity": "CRITICAL",
                    "description": f"Cash reserve {cash_ratio:.2%} below 10%",
                    "value": cash_ratio,
                    "threshold": 0.10
                })


        
        # Gate passes only if no R0 or R1 violations
        gate_pass = len(violations) == 0
        
        return gate_pass, violations
    
    def calculate_r2_soft_rule_scores(
        self,
        account_id: int,
        start_time: datetime,
        end_time: datetime
    ) -> Dict[str, float]:
        """
        Calculate R2 soft rule compliance scores
        
        Args:
            account_id: Account ID
            start_time: Evaluation period start
            end_time: Evaluation period end
            
        Returns:
            Dict mapping rule_id to score (0-1 range)
        """
        from services.evaluation.metrics_calculator import MetricsCalculator
        
        calculator = MetricsCalculator(self.db)
        scores = {}
        
        # Get snapshots in range
        snapshots = self.db.query(AccountSnapshot).filter(
            AccountSnapshot.account_id == account_id,
            AccountSnapshot.ts >= start_time,
            AccountSnapshot.ts <= end_time
        ).order_by(AccountSnapshot.ts).all()
        
        if len(snapshots) < 2:
            logger.warning(f"Insufficient snapshots for account {account_id}, using default scores")
            return {
                "R2-01": 1.0,  # Volatility
                "R2-02": 1.0,  # Turnover
                "R2-03": 1.0,  # Sector preference
                "R2-04": 1.0,  # Cash drag
                "R2-05": 1.0,  # Transaction cost
                "R2-06": 1.0   # Active engagement
            }
        
        # R2-01: Volatility (target: 10-15%)
        # Using non-linear (quadratic) penalty to increase discrimination
        volatility = calculator.calculate_volatility(snapshots)
        target_min = 0.10
        target_max = 0.15
        
        if volatility < target_min:
            # Below minimum - quadratic penalty (5% volatility = 0.5 deviation)
            deviation = (target_min - volatility) / target_min
            # Examples:
            # - 9% (dev=0.1) → 1-0.01 = 0.990
            # - 7.5% (dev=0.25) → 1-0.0625 = 0.938
            # - 5% (dev=0.5) → 1-0.25 = 0.750
            # - 0% (dev=1.0) → 1-1.0 = 0.000
            scores["R2-01"] = max(0, 1 - deviation ** 2)
        elif volatility > target_max:
            # Check if Sharpe ratio is high enough to justify higher volatility
            sharpe = calculator.calculate_sharpe_ratio(snapshots)
            sharpe_override_threshold = 2.0
            
            # Normalized deviation (30% volatility = 1.0 deviation)
            max_deviation_ref = 0.15  # 30% vol represents full deviation
            deviation = (volatility - target_max) / max_deviation_ref
            
            # Quadratic penalty with Sharpe override
            # Examples (no override):
            # - 17% (dev=0.133) → 1-0.018 = 0.982
            # - 20% (dev=0.333) → 1-0.111 = 0.889
            # - 25% (dev=0.667) → 1-0.445 = 0.555
            # - 30% (dev=1.0) → 1-1.0 = 0.000
            base_score = max(0, 1 - deviation ** 2)
            
            if sharpe >= sharpe_override_threshold:
                # High Sharpe ratio - allow higher volatility with reduced penalty (min 0.7)
                scores["R2-01"] = max(0.7, base_score)
            else:
                # No override - full penalty
                scores["R2-01"] = base_score
        else:
            # Within target range
            scores["R2-01"] = 1.0
        
        # R2-02: Turnover (target: <= 200%)
        # Using non-linear (quadratic) penalty to increase discrimination at extremes
        turnover = calculator.calculate_turnover(account_id, snapshots)
        target_turnover = 2.0  # 200%
        
        if turnover > target_turnover:
            # Normalized deviation (400% turnover = 1.0 deviation)
            deviation = (turnover - target_turnover) / target_turnover
            
            # Quadratic penalty: small deviations penalized lightly, large deviations heavily
            # Examples:
            # - 220% (dev=0.1) → 1-0.01 = 0.990 (very light penalty)
            # - 250% (dev=0.25) → 1-0.0625 = 0.938 (light penalty)
            # - 300% (dev=0.5) → 1-0.25 = 0.750 (moderate penalty)
            # - 400% (dev=1.0) → 1-1.0 = 0.000 (severe penalty)
            scores["R2-02"] = max(0, 1 - deviation ** 2)
        else:
            scores["R2-02"] = 1.0
        
        # R2-03: Sector preference (target: 30-60% in smart contract platforms)
        from services.agent.rule_aware.rule_engine import RuleEngine
        
        rule_engine = RuleEngine()
        r2_rules = rule_engine.load_rules("r2_client_soft")
        r2_03_rule = next((r for r in r2_rules if r.get("id") == "R2-03"), None)
        
        if r2_03_rule:
            params = r2_03_rule.get("parameters", {})
            preferred_sectors = params.get("preferred_sectors", [])
            preferred_crypto_themes = params.get("preferred_crypto_themes", [])
            target_min = params.get("target_allocation_min", 0.30)
            target_max = params.get("target_allocation_max", 0.60)
            
            # Combine all preferred themes
            all_preferred = set(s.lower() for s in (preferred_sectors + preferred_crypto_themes))
            
            sector_allocation = calculator.calculate_sector_allocation(account_id)
            
            # Calculate allocation in preferred sectors/themes using built-in mapping
            preferred_allocation = 0
            for sector, allocation in sector_allocation.items():
                if not sector:
                    continue
                # Use built-in CRYPTO_SECTOR_MAP to get actual sector
                # sector here is the symbol, we need to map it
                # Actually sector_allocation returns {sector_name: allocation}
                # We need to check if the sector matches preferred themes
                sector_normalized = sector.strip().lower()
                if sector_normalized in all_preferred:
                    preferred_allocation += allocation
            
            # Using non-linear (quadratic) penalty for better discrimination
            if preferred_allocation < target_min:
                # Below minimum - quadratic penalty from target_min
                # Treats "should have more" as deviation from target_min
                # Examples (target_min=0.30):
                # - 27% (dev=0.1) → 1-0.01 = 0.990
                # - 22.5% (dev=0.25) → 1-0.0625 = 0.938
                # - 15% (dev=0.5) → 1-0.25 = 0.750
                # - 0% (dev=1.0) → 1-1.0 = 0.000
                deviation = (target_min - preferred_allocation) / target_min
                scores["R2-03"] = max(0, 1 - deviation ** 2)
            elif preferred_allocation > target_max:
                # Above maximum - quadratic penalty for over-concentration
                # Normalized to 100% = 1.0 deviation (complete concentration)
                # Examples (target_max=0.70):
                # - 73% (dev=0.1) → 1-0.01 = 0.990
                # - 77.5% (dev=0.25) → 1-0.0625 = 0.938
                # - 85% (dev=0.5) → 1-0.25 = 0.750
                # - 100% (dev=1.0) → 1-1.0 = 0.000
                max_deviation_ref = 1.0 - target_max  # Distance to 100%
                deviation = (preferred_allocation - target_max) / max_deviation_ref
                base_score = max(0, 1 - deviation ** 2)
                # Allow minimum 0.7 for temporary over-allocation
                scores["R2-03"] = max(0.7, base_score)
            else:
                # Within target range
                scores["R2-03"] = 1.0
        else:
            # Rule not found - default score
            scores["R2-03"] = 1.0
        
        # R2-04: Cash drag (target: <= 20%)
        # Using non-linear (quadratic) penalty to increase discrimination
        latest_snapshot = snapshots[-1]
        cash_ratio = float(latest_snapshot.cash / latest_snapshot.total_equity) if latest_snapshot.total_equity > 0 else 0
        target_cash = 0.20  # 20%
        
        if cash_ratio > target_cash:
            # High cash position - check duration
            extended_period_days = 7
            
            # Count how many recent snapshots have >20% cash
            high_cash_count = sum(1 for s in snapshots[-extended_period_days*24:] 
                                 if float(s.cash / s.total_equity) > target_cash if s.total_equity > 0)
            
            is_extended = high_cash_count > extended_period_days * 24 * 0.8
            
            # Normalized deviation (50% cash = 1.0 deviation for severe case)
            max_deviation_ref = 0.30  # 50% cash represents 1.0 normalized deviation
            deviation = (cash_ratio - target_cash) / max_deviation_ref
            
            # Quadratic penalty with different severity based on duration
            # Examples (extended):
            # - 25% (dev=0.167) → 1-0.028 = 0.972 (light penalty)
            # - 30% (dev=0.333) → 1-0.111 = 0.889 (moderate penalty)
            # - 40% (dev=0.667) → 1-0.445 = 0.555 (heavy penalty)
            # - 50% (dev=1.0) → 1-1.0 = 0.000 (severe penalty)
            base_score = max(0, 1 - deviation ** 2)
            
            if is_extended:
                # Extended period with high cash - full penalty
                scores["R2-04"] = base_score
            else:
                # Temporary high cash - reduced penalty (min 0.7)
                scores["R2-04"] = max(0.7, base_score)
        else:
            scores["R2-04"] = 1.0
        
        # R2-05: Transaction cost minimization (Fee Sensitivity)
        # Using non-linear (quadratic) penalty to increase discrimination
        # Load R2-05 rule parameters from configuration
        r2_05_rule = next((r for r in r2_rules if r.get("id") == "R2-05"), None)
        evaluation_cost_threshold = 0.0015  # Default: 0.15%
        
        if r2_05_rule:
            params = r2_05_rule.get("parameters", {})
            evaluation_cost_threshold = params.get("evaluation_cost_threshold", 0.0015)
        
        avg_cost = calculator.calculate_avg_transaction_cost(account_id, start_time, end_time)
        
        if avg_cost > evaluation_cost_threshold:
            # Normalized deviation (0.30% cost = 1.0 deviation)
            max_cost_ref = 0.0015  # 0.30% represents full deviation
            deviation = (avg_cost - evaluation_cost_threshold) / max_cost_ref
            
            # Quadratic penalty: small cost overruns penalized lightly, large ones heavily
            # Examples:
            # - 0.165% (dev=0.1) → 1-0.01 = 0.990
            # - 0.1875% (dev=0.25) → 1-0.0625 = 0.938
            # - 0.225% (dev=0.5) → 1-0.25 = 0.750
            # - 0.30% (dev=1.0) → 1-1.0 = 0.000
            scores["R2-05"] = max(0, 1 - deviation ** 2)
        else:
            scores["R2-05"] = 1.0
        
        # R2-06: Active Engagement (Avoid Strategic Inertia)
        # Using non-linear (quadratic) penalty for consecutive HOLD actions
        # Load R2-06 rule parameters from configuration
        r2_06_rule = next((r for r in r2_rules if r.get("id") == "R2-06"), None)
        max_consecutive_before_penalty = 1  # Default: penalty starts at 2nd consecutive HOLD
        penalty_max_reference = 3  # Default: 4 consecutive HOLDs = score 0 (更激进的衰减)
        low_volatility_threshold = 0.005  # Default: 0.5% daily volatility exempts from penalty
        
        if r2_06_rule:
            params = r2_06_rule.get("parameters", {})
            max_consecutive_before_penalty = params.get("max_consecutive_holds_before_penalty", 1)
            penalty_max_reference = params.get("hold_penalty_max_reference", 3)
            low_volatility_threshold = params.get("low_volatility_threshold", 0.005)
        
        # Count consecutive HOLD decisions
        consecutive_holds = self._count_consecutive_holds(account_id, end_time)
        
        # Check if low volatility exemption applies
        recent_volatility = calculator.calculate_volatility(snapshots[-min(7*24, len(snapshots)):])  # Last 7 days
        is_low_volatility = recent_volatility < low_volatility_threshold
        
        if consecutive_holds <= max_consecutive_before_penalty or is_low_volatility:
            # No penalty: either within acceptable range or justified by low volatility
            scores["R2-06"] = 1.0
            if is_low_volatility and consecutive_holds > max_consecutive_before_penalty:
                logger.info(f"R2-06: {consecutive_holds} consecutive HOLDs exempted due to low volatility ({recent_volatility:.2%})")
        else:
            # Progressive quadratic penalty starting from 2nd consecutive HOLD
            # Formula: score = max(0, 1 - ((n - 1) / penalty_max_ref)^2)
            # Examples (penalty_max_ref=3, 更快衰减):
            # - n=2: 1 - (1/3)^2 = 0.889 (-11%)
            # - n=3: 1 - (2/3)^2 = 0.556 (-44%)
            # - n=4: 1 - (3/3)^2 = 0.000 (-100%)
            deviation = (consecutive_holds - 1) / penalty_max_reference
            scores["R2-06"] = max(0, 1 - deviation ** 2)
            logger.warning(
                f"R2-06: {consecutive_holds} consecutive HOLD actions detected, "
                f"score={scores['R2-06']:.3f} (volatility={recent_volatility:.2%})"
            )
        
        return scores
    
    def _count_consecutive_holds(self, account_id: int, end_time: datetime) -> int:
        """
        Count consecutive HOLD decisions leading up to end_time
        
        Args:
            account_id: Account ID
            end_time: End time for counting
            
        Returns:
            Number of consecutive HOLD actions (0 if last action was a trade)
        """
        # Query recent decisions in reverse chronological order
        recent_decisions = self.db.query(AIDecisionLog).filter(
            AIDecisionLog.account_id == account_id,
            AIDecisionLog.decision_time <= end_time
        ).order_by(AIDecisionLog.decision_time.desc()).limit(20).all()  # Look back max 20 decisions
        
        if not recent_decisions:
            return 0
        
        consecutive_holds = 0
        for decision in recent_decisions:
            operation = decision.operation.lower() if decision.operation else ""
            
            # Check if this is a HOLD action
            # HOLD can be represented as: "hold", "HOLD", None operation, or empty string
            is_hold = operation in ["hold", ""] or operation is None
            
            if is_hold:
                consecutive_holds += 1
            else:
                # Found an actual trade, stop counting
                break
        
        return consecutive_holds
    
    def save_evaluation_result(
        self,
        trace_id: str,
        account_id: int,
        gate_pass: bool,
        r0_violations: List[Dict],
        r1_violations: List[Dict],
        r2_scores: Dict[str, float],
        s_audit: Optional[float] = None,
        lambda_weight: float = 0.5
    ) -> RuleEvaluationResult:
        """
        Save evaluation result to database
        
        Args:
            trace_id: Decision trace ID
            account_id: Account ID
            gate_pass: Whether R0/R1 gate passed
            r0_violations: R0 rule violations
            r1_violations: R1 rule violations
            r2_scores: R2 soft rule scores
            s_audit: LLM audit score (0-1)
            lambda_weight: Weight for S_rule_sat vs S_audit
            
        Returns:
            Created RuleEvaluationResult
        """
        import json
        
        # Calculate S_rule_sat (weighted average of R2 scores)
        r2_weights = {
            "R2-01": 0.15,  # Volatility
            "R2-02": 0.20,  # Turnover
            "R2-03": 0.15,  # Sector preference
            "R2-04": 0.15,  # Cash drag
            "R2-05": 0.15,  # Transaction cost
            "R2-06": 0.20   # Active engagement
        }
        
        s_rule_sat = sum(r2_scores.get(rule_id, 0) * weight for rule_id, weight in r2_weights.items())
        
        # Calculate final score
        # Score = Gate(R0,R1) × (λ × S_rule_sat + (1-λ) × S_audit)
        if s_audit is None:
            s_audit = 0.0  # Default if LLM audit not available
        
        if gate_pass:
            final_score = lambda_weight * s_rule_sat + (1 - lambda_weight) * s_audit
        else:
            final_score = 0.0  # Gate failed
        
        # Create evaluation result
        result = RuleEvaluationResult(
            trace_id=trace_id,
            account_id=account_id,
            ts=datetime.now(),
            gate_pass="true" if gate_pass else "false",
            r0_violations_json=json.dumps(r0_violations) if r0_violations else None,
            r1_violations_json=json.dumps(r1_violations) if r1_violations else None,
            r2_scores_json=json.dumps(r2_scores),
            s_rule_sat=s_rule_sat,
            s_audit=s_audit,
            final_score=final_score
        )
        
        self.db.add(result)
        self.db.commit()
        
        logger.info(
            f"Evaluation saved for trace {trace_id}: "
            f"gate_pass={gate_pass}, s_rule_sat={s_rule_sat:.3f}, "
            f"s_audit={s_audit:.3f}, final_score={final_score:.3f}"
        )
        
        return result
