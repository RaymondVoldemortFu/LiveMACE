"""
Risk Event Evaluator

Compares risk metrics between memory-enabled and memory-disabled agents:
1. Drawdown Events - Sharp declines from peak
2. Sharp Movement Events - Extreme gains/losses in single period
3. Consecutive Loss Streaks - Multiple losing periods in a row
4. Tail Risk - Frequency and magnitude of extreme losses
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass
import logging
import numpy as np
from sqlalchemy.orm import Session

from .base import BaseEvaluator
from database.models import AccountSnapshot

logger = logging.getLogger(__name__)


@dataclass
class CheckpointData:
    """Unified checkpoint data from account_snapshots."""
    period_end: datetime
    equity_end: float
    return_rate: float
    pnl: float


class RiskEvaluator(BaseEvaluator):
    """Evaluates risk events to measure memory's impact on decision quality."""

    # Configurable thresholds
    DRAWDOWN_THRESHOLD = 0.05  # 5% decline from peak
    SHARP_MOVEMENT_THRESHOLD = 0.01  # 1% single-period change
    TAIL_RISK_PERCENTILE = 0.05  # Bottom 5% of returns
    LOSS_STREAK_MIN_LENGTH = 3  # 3+ consecutive losses


    def __init__(self, db: Session):
        self.db = db

    @property
    def name(self) -> str:
        return "Risk Event Evaluator"

    def evaluate(self, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate risk events for an account.

        Args:
            agent_data: Must contain 'account_id', optional 'start_time', 'end_time'

        Returns:
            Risk metrics including drawdowns, sharp movements, loss streaks
        """
        account_id = agent_data.get("account_id")
        if not account_id:
            raise ValueError("account_id is required")

        # Load checkpoints
        checkpoints = self._load_checkpoints(
            account_id,
            agent_data.get("start_time"),
            agent_data.get("end_time")
        )

        if len(checkpoints) < 2:
            return {"error": "Insufficient data (need at least 2 checkpoints)"}

        # Calculate all risk metrics
        return {
            "account_id": account_id,
            "total_checkpoints": len(checkpoints),
            "drawdown_events": self._detect_drawdowns(checkpoints),
            "sharp_movements": self._detect_sharp_movements(checkpoints),
            "loss_streaks": self._detect_loss_streaks(checkpoints),
            "tail_risk": self._calculate_tail_risk(checkpoints),
            "evaluated_at": datetime.now().isoformat()
        }

    def _load_checkpoints(
        self,
        account_id: int,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[CheckpointData]:
        """Load account snapshots ordered by time and compute return rates."""
        query = self.db.query(AccountSnapshot).filter(
            AccountSnapshot.account_id == account_id
        )
        if start_time:
            query = query.filter(AccountSnapshot.ts >= start_time)
        if end_time:
            query = query.filter(AccountSnapshot.ts <= end_time)

        snapshots = query.order_by(AccountSnapshot.ts).all()

        if len(snapshots) < 2:
            return []

        checkpoints = []
        for i in range(1, len(snapshots)):
            prev = snapshots[i - 1]
            curr = snapshots[i]
            prev_equity = float(prev.total_equity)
            curr_equity = float(curr.total_equity)
            return_rate = (curr_equity - prev_equity) / prev_equity if prev_equity != 0 else 0.0
            pnl = curr_equity - prev_equity
            checkpoints.append(CheckpointData(
                period_end=curr.ts if isinstance(curr.ts, datetime) else datetime.fromisoformat(str(curr.ts)),
                equity_end=curr_equity,
                return_rate=round(return_rate, 6),
                pnl=round(pnl, 4)
            ))

        return checkpoints

    def _detect_drawdowns(self, checkpoints: List[CheckpointData]) -> Dict[str, Any]:
        """Detect drawdown events (peak-to-trough declines)."""
        equities = [float(cp.equity_end) for cp in checkpoints]

        drawdowns = []
        peak_equity = equities[0]
        peak_idx = 0
        in_drawdown = False
        trough_equity = peak_equity
        trough_idx = 0

        for i, equity in enumerate(equities):
            if equity > peak_equity:
                # New peak reached
                if in_drawdown:
                    # End of drawdown, record it
                    drawdown_pct = (trough_equity - peak_equity) / peak_equity
                    if drawdown_pct <= -self.DRAWDOWN_THRESHOLD:
                        drawdowns.append({
                            "peak_time": checkpoints[peak_idx].period_end.isoformat(),
                            "trough_time": checkpoints[trough_idx].period_end.isoformat(),
                            "drawdown_pct": round(drawdown_pct, 4),
                            "peak_equity": float(peak_equity),
                            "trough_equity": float(trough_equity)
                        })
                    in_drawdown = False

                peak_equity = equity
                peak_idx = i
                trough_equity = equity
                trough_idx = i
            else:
                # Potential drawdown
                if equity < trough_equity:
                    trough_equity = equity
                    trough_idx = i
                    in_drawdown = True

        # Check final drawdown
        if in_drawdown:
            drawdown_pct = (trough_equity - peak_equity) / peak_equity
            if drawdown_pct <= -self.DRAWDOWN_THRESHOLD:
                drawdowns.append({
                    "peak_time": checkpoints[peak_idx].period_end.isoformat(),
                    "trough_time": checkpoints[trough_idx].period_end.isoformat(),
                    "drawdown_pct": round(drawdown_pct, 4),
                    "peak_equity": float(peak_equity),
                    "trough_equity": float(trough_equity)
                })

        return {
            "count": len(drawdowns),
            "events": drawdowns,
            "max_drawdown": min([d["drawdown_pct"] for d in drawdowns]) if drawdowns else 0.0
        }

    def _detect_sharp_movements(self, checkpoints: List[CheckpointData]) -> Dict[str, Any]:
        """Detect sharp single-period movements (gains and losses)."""
        sharp_losses = []
        sharp_gains = []

        for cp in checkpoints:
            return_rate = float(cp.return_rate)

            if return_rate <= -self.SHARP_MOVEMENT_THRESHOLD:
                sharp_losses.append({
                    "time": cp.period_end.isoformat(),
                    "return_rate": round(return_rate, 4),
                    "pnl": float(cp.pnl)
                })
            elif return_rate >= self.SHARP_MOVEMENT_THRESHOLD:
                sharp_gains.append({
                    "time": cp.period_end.isoformat(),
                    "return_rate": round(return_rate, 4),
                    "pnl": float(cp.pnl)
                })

        return {
            "sharp_losses": {
                "count": len(sharp_losses),
                "events": sharp_losses
            },
            "sharp_gains": {
                "count": len(sharp_gains),
                "events": sharp_gains
            }
        }

    def _detect_loss_streaks(self, checkpoints: List[CheckpointData]) -> Dict[str, Any]:
        """Detect consecutive loss streaks."""
        streaks = []
        current_streak = []

        for cp in checkpoints:
            if float(cp.return_rate) < 0:
                current_streak.append({
                    "time": cp.period_end.isoformat(),
                    "return_rate": round(float(cp.return_rate), 4)
                })
            else:
                if len(current_streak) >= self.LOSS_STREAK_MIN_LENGTH:
                    streaks.append({
                        "length": len(current_streak),
                        "periods": current_streak,
                        "total_loss": round(sum(p["return_rate"] for p in current_streak), 4)
                    })
                current_streak = []

        # Check final streak
        if len(current_streak) >= self.LOSS_STREAK_MIN_LENGTH:
            streaks.append({
                "length": len(current_streak),
                "periods": current_streak,
                "total_loss": round(sum(p["return_rate"] for p in current_streak), 4)
            })

        return {
            "count": len(streaks),
            "events": streaks,
            "max_streak_length": max([s["length"] for s in streaks]) if streaks else 0
        }

    def _calculate_tail_risk(self, checkpoints: List[CheckpointData]) -> Dict[str, Any]:
        """Calculate tail risk (extreme losses)."""
        returns = [float(cp.return_rate) for cp in checkpoints]

        if not returns:
            return {"error": "No returns data"}

        returns_array = np.array(returns)
        threshold = np.percentile(returns_array, self.TAIL_RISK_PERCENTILE * 100)

        tail_events = [
            {
                "time": cp.period_end.isoformat(),
                "return_rate": round(float(cp.return_rate), 4)
            }
            for cp in checkpoints
            if float(cp.return_rate) <= threshold
        ]

        return {
            "threshold": round(float(threshold), 4),
            "count": len(tail_events),
            "events": tail_events,
            "avg_tail_loss": round(float(np.mean([e["return_rate"] for e in tail_events])), 4) if tail_events else 0.0
        }

    def compare_accounts(
        self,
        memory_account_id: int,
        baseline_account_id: int,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Compare risk metrics between memory-enabled and baseline accounts."""
        memory_results = self.evaluate({
            "account_id": memory_account_id,
            "start_time": start_time,
            "end_time": end_time
        })

        baseline_results = self.evaluate({
            "account_id": baseline_account_id,
            "start_time": start_time,
            "end_time": end_time
        })

        return {
            "memory_enabled": memory_results,
            "baseline": baseline_results,
            "comparison": {
                "drawdown_reduction": baseline_results["drawdown_events"]["count"] - memory_results["drawdown_events"]["count"],
                "sharp_loss_reduction": baseline_results["sharp_movements"]["sharp_losses"]["count"] - memory_results["sharp_movements"]["sharp_losses"]["count"],
                "loss_streak_reduction": baseline_results["loss_streaks"]["count"] - memory_results["loss_streaks"]["count"],
                "tail_risk_reduction": baseline_results["tail_risk"]["count"] - memory_results["tail_risk"]["count"]
            }
        }


def main():
    from database.connection import get_db
    from database.models import Account

    db = next(get_db())
    evaluator = RiskEvaluator(db)
    accounts = db.query(Account).filter(Account.is_active == "true").all()

    print("=" * 60)
    print("Risk Event Evaluation Report")
    print("=" * 60)

    for account in accounts:
        sep = "=" * 60
        print(f"\n{sep}")
        print(f"Account: {account.name} (ID: {account.id})")
        print(f"Memory: {account.memory_enabled}")
        print(sep)

        results = evaluator.evaluate({"account_id": account.id})

        if "error" in results:
            print(f"  Warning: {results['error']}")
            continue

        print(f"\nCheckpoints: {results['total_checkpoints']}")

        dd = results['drawdown_events']
        print(f"\nDrawdowns: {dd['count']}")
        if dd['count'] > 0:
            print(f"  Max: {dd['max_drawdown']:.2%}")

        sm = results['sharp_movements']
        print(f"\nSharp Movements:")
        print(f"  Losses: {sm['sharp_losses']['count']}")
        print(f"  Gains: {sm['sharp_gains']['count']}")

        ls = results['loss_streaks']
        print(f"\nLoss Streaks: {ls['count']}")
        if ls['count'] > 0:
            print(f"  Max: {ls['max_streak_length']} periods")

        tr = results['tail_risk']
        print(f"\nTail Risk: {tr['count']}")
        if tr['count'] > 0:
            print(f"  Threshold: {tr['threshold']:.2%}")
            print(f"  Avg Loss: {tr['avg_tail_loss']:.2%}")

    print(f"\n{'=' * 60}")
    print("Complete")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()

