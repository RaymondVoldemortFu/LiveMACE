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
        # Need to check account snapshots for equity calculation
        latest_snapshot = self.db.query(AccountSnapshot).filter(
            AccountSnapshot.account_id == account_id
        ).order_by(AccountSnapshot.ts.desc()).first()
        
        if latest_snapshot:
            initial_equity = latest_snapshot.total_equity
            current_equity = account.total_asset
            drawdown = (initial_equity - current_equity) / initial_equity if initial_equity > 0 else 0
            
            if drawdown > 0.05:
                violations.append({
                    "rule": "R0-03",
                    "level": "R0_SYSTEM_HARD",
                    "severity": "CRITICAL",
                    "description": f"Intraday drawdown {drawdown:.2%} exceeds 5%",
                    "value": drawdown,
                    "threshold": 0.05
                })
        
        # R0-04: Single order size <= 20% of total equity
        if decision.operation in ['buy', 'sell'] and latest_snapshot:
            order_value = abs(decision.leverage or 1) * float(account.current_cash) * 0.2  # Approximate
            max_order_value = latest_snapshot.total_equity * 0.20
            
            if order_value > max_order_value:
                violations.append({
                    "rule": "R0-04",
                    "level": "R0_SYSTEM_HARD",
                    "severity": "CRITICAL",
                    "description": f"Order size exceeds 20% of total equity",
                    "value": order_value / latest_snapshot.total_equity,
                    "threshold": 0.20
                })
        
        # R1-01: Client blacklist (check asset metadata)
        if decision.symbol:
            asset_meta = self.db.query(AssetMetadata).filter(
                AssetMetadata.symbol == decision.symbol
            ).first()
            
            # Example: If client blacklists meme coins
            if asset_meta and asset_meta.is_meme == "true":
                violations.append({
                    "rule": "R1-01",
                    "level": "R1_CLIENT_HARD",
                    "severity": "CRITICAL",
                    "description": f"Trading blacklisted asset: {decision.symbol} (meme coin)",
                    "value": decision.symbol,
                    "threshold": "NO_MEME_COINS"
                })
        
        # R1-04: Minimum cash reserve >= 10%
        if latest_snapshot:
            cash_ratio = latest_snapshot.cash / latest_snapshot.total_equity if latest_snapshot.total_equity > 0 else 1
            if cash_ratio < 0.10:
                violations.append({
                    "rule": "R1-04",
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
            scores["R2-01"] = max(0, 1 - (0.10 - volatility) * 5)  # Exponential penalty
        elif volatility > 0.15:
            scores["R2-01"] = max(0, 1 - (volatility - 0.15) * 5)
        else:
            scores["R2-01"] = 1.0
        
        # R2-02: Turnover (target: <= 200%)
        turnover = calculator.calculate_turnover(account_id, snapshots)
        if turnover > 2.0:
            scores["R2-02"] = max(0, 1 - (turnover - 2.0) * 0.5)
        else:
            scores["R2-02"] = 1.0
        
        # R2-03: Sector preference (target: >= 40% in preferred sector)
        sector_allocation = calculator.calculate_sector_allocation(account_id)
        preferred_sector_ratio = sector_allocation.get("Layer1", 0)  # Example: prefer Layer1
        if preferred_sector_ratio < 0.40:
            scores["R2-03"] = max(0, preferred_sector_ratio / 0.40)
        else:
            scores["R2-03"] = 1.0
        
        # R2-04: Cash drag (target: <= 20%)
        latest_snapshot = snapshots[-1]
        cash_ratio = float(latest_snapshot.cash / latest_snapshot.total_equity) if latest_snapshot.total_equity > 0 else 0
        if cash_ratio > 0.20:
            scores["R2-04"] = max(0, 1 - (cash_ratio - 0.20) * 2)
        else:
            scores["R2-04"] = 1.0
        
        # R2-05: Transaction cost minimization
        avg_cost = calculator.calculate_avg_transaction_cost(account_id, start_time, end_time)
        # Assume 0.1% is ideal, penalize if higher
        if avg_cost > 0.001:
            scores["R2-05"] = max(0, 1 - (avg_cost - 0.001) * 100)
        else:
            scores["R2-05"] = 1.0
        
        # R2-06: Sharpe ratio (target: >= 1.5)
        sharpe = calculator.calculate_sharpe_ratio(snapshots)
        if sharpe < 1.5:
            scores["R2-06"] = max(0, sharpe / 1.5)
        else:
            scores["R2-06"] = 1.0
        
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
