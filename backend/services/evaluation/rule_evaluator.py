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
            from backend.config.rules.rule_engine import RuleEngine
            
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
        if decision.symbol and latest_snapshot:
            from backend.database.models import Position
            
            # Get all current positions for this account
            positions = self.db.query(Position).filter(
                Position.account_id == account_id
            ).all()
            
            total_equity = float(latest_snapshot.total_equity)
            if total_equity > 0:
                for pos in positions:
                    # Calculate position notional value (absolute value for long/short)
                    position_value = abs(float(pos.quantity) * float(pos.current_price or 0))
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
                "R2-06": 1.0   # Sharpe ratio
            }
        
        # R2-01: Volatility (target: 10-15%)
        volatility = calculator.calculate_volatility(snapshots)
        if volatility < 0.10:
            # Below minimum - penalize proportionally
            scores["R2-01"] = max(0, 1 - (0.10 - volatility) * 5)
        elif volatility > 0.15:
            # Check if Sharpe ratio is high enough to justify higher volatility
            sharpe = calculator.calculate_sharpe_ratio(snapshots)
            sharpe_override_threshold = 2.0
            
            if sharpe >= sharpe_override_threshold:
                # High Sharpe ratio - allow higher volatility with reduced penalty
                scores["R2-01"] = max(0.7, 1 - (volatility - 0.15) * 2)
            else:
                # No override - standard penalty
                scores["R2-01"] = max(0, 1 - (volatility - 0.15) * 5)
        else:
            # Within target range
            scores["R2-01"] = 1.0
        
        # R2-02: Turnover (target: <= 200%)
        turnover = calculator.calculate_turnover(account_id, snapshots)
        penalty_threshold = 3.0  # 300% turnover triggers severe penalty
        
        if turnover > 2.0:
            if turnover > penalty_threshold:
                # Excessive noise trading - severe penalty
                scores["R2-02"] = max(0, 1 - (turnover - 2.0) * 1.0)
            else:
                # Moderate excess - standard penalty
                scores["R2-02"] = max(0, 1 - (turnover - 2.0) * 0.5)
        else:
            scores["R2-02"] = 1.0
        
        # R2-03: Sector preference (target: 30-50% in preferred sectors)
        from backend.config.rules.rule_engine import RuleEngine
        
        rule_engine = RuleEngine()
        r2_rules = rule_engine.load_rules("r2_client_soft")
        r2_03_rule = next((r for r in r2_rules if r.get("id") == "R2-03"), None)
        
        if r2_03_rule:
            params = r2_03_rule.get("parameters", {})
            preferred_sectors = params.get("preferred_sectors", [])
            preferred_crypto_themes = params.get("preferred_crypto_themes", [])
            target_min = params.get("target_allocation_min", 0.30)
            target_max = params.get("target_allocation_max", 0.50)
            
            sector_allocation = calculator.calculate_sector_allocation(account_id)
            
            # Calculate allocation in preferred sectors/themes
            preferred_allocation = 0
            for sector, allocation in sector_allocation.items():
                sector_lower = sector.lower() if sector else ""
                # Check if sector matches any preferred sector or crypto theme
                if any(s.lower() in sector_lower or sector_lower in s.lower() 
                       for s in (preferred_sectors + preferred_crypto_themes)):
                    preferred_allocation += allocation
            
            if preferred_allocation < target_min:
                # Below minimum - proportional penalty
                scores["R2-03"] = max(0, preferred_allocation / target_min)
            elif preferred_allocation > target_max:
                # Above maximum - slight penalty for over-concentration
                scores["R2-03"] = max(0.7, 1 - (preferred_allocation - target_max) * 2)
            else:
                # Within target range
                scores["R2-03"] = 1.0
        else:
            # Rule not found - default score
            scores["R2-03"] = 1.0
        
        # R2-04: Cash drag (target: <= 20%)
        latest_snapshot = snapshots[-1]
        cash_ratio = float(latest_snapshot.cash / latest_snapshot.total_equity) if latest_snapshot.total_equity > 0 else 0
        
        if cash_ratio > 0.20:
            # High cash position - check duration
            extended_period_days = 7
            
            # Count how many recent snapshots have >20% cash
            high_cash_count = sum(1 for s in snapshots[-extended_period_days*24:] 
                                 if float(s.cash / s.total_equity) > 0.20 if s.total_equity > 0)
            
            if high_cash_count > extended_period_days * 24 * 0.8:
                # Extended period with high cash - penalty
                scores["R2-04"] = max(0, 1 - (cash_ratio - 0.20) * 3)
            else:
                # Temporary high cash - reduced penalty
                scores["R2-04"] = max(0.7, 1 - (cash_ratio - 0.20) * 1.5)
        else:
            scores["R2-04"] = 1.0
        
        # R2-05: Transaction cost minimization
        avg_cost = calculator.calculate_avg_transaction_cost(account_id, start_time, end_time)
        target_max_slippage = 0.001  # 0.1% target
        
        if avg_cost > target_max_slippage:
            # Higher than target - penalty
            scores["R2-05"] = max(0, 1 - (avg_cost - target_max_slippage) * 100)
        else:
            scores["R2-05"] = 1.0
        
        # R2-06: Sharpe ratio (target: >= 1.5)
        sharpe = calculator.calculate_sharpe_ratio(snapshots)
        
        if sharpe < 1.5:
            # Below target - proportional score
            scores["R2-06"] = max(0, sharpe / 1.5) if sharpe > 0 else 0
        else:
            # Meets or exceeds target
            scores["R2-06"] = min(1.0, sharpe / 1.5)  # Cap at 1.0
        
        return scores
    
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
            "R2-01": 0.20,  # Volatility
            "R2-02": 0.15,  # Turnover
            "R2-03": 0.25,  # Sector preference
            "R2-04": 0.10,  # Cash drag
            "R2-05": 0.10,  # Transaction cost
            "R2-06": 0.20   # Sharpe ratio
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
