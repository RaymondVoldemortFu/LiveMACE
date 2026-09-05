from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from database.models import AIDecisionLog, RuleEvaluationResult


class ComplianceApiService:
    def __init__(self, db: Session):
        self.db = db

    def history(self, account_id: int, limit: int, offset: int):
        query = (
            self.db.query(RuleEvaluationResult)
            .filter(RuleEvaluationResult.account_id == account_id)
            .order_by(RuleEvaluationResult.ts.desc())
        )
        records = query.offset(offset).limit(limit).all()
        return {
            "total": query.count(),
            "limit": limit,
            "offset": offset,
            "records": [
                {
                    "id": record.id,
                    "timestamp": record.ts.isoformat() if record.ts else None,
                    "trace_id": record.trace_id,
                    "gate_pass": record.gate_pass == "true",
                    "s_rule_sat": round(record.s_rule_sat, 3) if record.s_rule_sat is not None else None,
                    "s_audit": round(record.s_audit, 3) if record.s_audit is not None else None,
                    "final_score": round(record.final_score, 3) if record.final_score is not None else None,
                }
                for record in records
            ],
        }

    def trend(self, account_id: int, period: str, metric: str):
        days_back = {"day": 30, "week": 90, "month": 365}[period]
        records = (
            self.db.query(RuleEvaluationResult)
            .filter(
                RuleEvaluationResult.account_id == account_id,
                RuleEvaluationResult.ts >= datetime.utcnow() - timedelta(days=days_back),
            )
            .order_by(RuleEvaluationResult.ts.asc())
            .all()
        )
        daily_data = defaultdict(list)
        for record in records:
            date_key = record.ts.date().isoformat() if record.ts else None
            if not date_key:
                continue
            if metric == "gate_pass_rate":
                daily_data[date_key].append(1 if record.gate_pass == "true" else 0)
            else:
                value = getattr(record, metric)
                if value is not None:
                    daily_data[date_key].append(value)
        return {
            "period": period,
            "metric": metric,
            "data_points": [
                {
                    "date": date,
                    "value": round(sum(daily_data[date]) / len(daily_data[date]), 3),
                    "count": len(daily_data[date]),
                }
                for date in sorted(daily_data)
                if daily_data[date]
            ],
        }

    @staticmethod
    def _aggregate(records):
        count = len(records)
        final_scores = [record.final_score for record in records if record.final_score is not None]
        rule_scores = [record.s_rule_sat for record in records if record.s_rule_sat is not None]
        audit_scores = [record.s_audit for record in records if record.s_audit is not None]
        return {
            "gate_pass_rate": round(sum(record.gate_pass == "true" for record in records) / count, 3)
            if count
            else 0,
            "avg_final_score": round(sum(final_scores) / len(final_scores), 3) if final_scores else None,
            "avg_s_rule_sat": round(sum(rule_scores) / len(rule_scores), 3) if rule_scores else None,
            "avg_s_audit": round(sum(audit_scores) / len(audit_scores), 3) if audit_scores else None,
            "evaluation_count": count,
        }

    def stats(self, account_id: int):
        records = self.db.query(RuleEvaluationResult).filter(
            RuleEvaluationResult.account_id == account_id
        ).all()
        if not records:
            return {
                "total_evaluations": 0,
                "all_time": None,
                "recent_7d": None,
                "llm_audit_stats": None,
            }
        recent = [
            record
            for record in records
            if record.ts and record.ts >= datetime.utcnow() - timedelta(days=7)
        ]
        llm_records = [record for record in records if record.llm_audit_score is not None]
        llm_stats = None
        if llm_records:
            scores = [record.llm_audit_score for record in llm_records if record.llm_audit_score is not None]
            coverage = [record.llm_audit_coverage for record in llm_records if record.llm_audit_coverage is not None]
            conflict = [record.llm_audit_conflict for record in llm_records if record.llm_audit_conflict is not None]
            llm_stats = {
                "count": len(llm_records),
                "avg_score": round(sum(scores) / len(scores), 3) if scores else None,
                "avg_coverage": round(sum(coverage) / len(coverage), 3) if coverage else None,
                "avg_conflict": round(sum(conflict) / len(conflict), 3) if conflict else None,
            }
        return {
            "total_evaluations": len(records),
            "all_time": self._aggregate(records),
            "recent_7d": self._aggregate(recent) if recent else None,
            "llm_audit_stats": llm_stats,
        }

    def recent_decisions(self, account_id: int, limit: int):
        decisions = (
            self.db.query(AIDecisionLog)
            .filter(AIDecisionLog.account_id == account_id)
            .order_by(AIDecisionLog.decision_time.desc())
            .limit(limit)
            .all()
        )
        result = []
        for decision in decisions:
            compliance = None
            if decision.trace_id:
                compliance = self.db.query(RuleEvaluationResult).filter(
                    RuleEvaluationResult.trace_id == decision.trace_id
                ).first()
            result.append(
                {
                    "trace_id": decision.trace_id,
                    "timestamp": decision.decision_time.isoformat() if decision.decision_time else None,
                    "operation": decision.operation,
                    "symbol": decision.symbol,
                    "leverage": decision.leverage,
                    "executed": decision.executed == "true",
                    "compliance": {
                        "gate_pass": compliance.gate_pass == "true",
                        "final_score": round(compliance.final_score, 3) if compliance.final_score else None,
                        "s_audit": round(compliance.s_audit, 3) if compliance.s_audit else None,
                    }
                    if compliance
                    else None,
                }
            )
        return {"account_id": account_id, "count": len(result), "decisions": result}
