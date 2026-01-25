"""
Compliance API Routes - Compliance history, trends, and statistics
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Optional
from datetime import datetime, timedelta
import logging

from database.connection import SessionLocal
from database.models import RuleEvaluationResult, Account, AIDecisionLog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/compliance", tags=["compliance"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/account/{account_id}/history")
async def get_compliance_history(
    account_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Get compliance evaluation history for an account
    
    Returns paginated list of compliance scores and basic statistics
    """
    try:
        # Query compliance records
        query = db.query(RuleEvaluationResult).filter(
            RuleEvaluationResult.account_id == account_id
        ).order_by(RuleEvaluationResult.ts.desc())
        
        # Get total count
        total = query.count()
        
        # Get paginated results
        records = query.offset(offset).limit(limit).all()
        
        # Format results
        result_list = []
        for record in records:
            result_list.append({
                "id": record.id,
                "timestamp": record.ts.isoformat() if record.ts else None,
                "trace_id": record.trace_id,
                "gate_pass": record.gate_pass == "true",
                "s_rule_sat": round(record.s_rule_sat, 3) if record.s_rule_sat is not None else None,
                "s_audit": round(record.s_audit, 3) if record.s_audit is not None else None,
                "final_score": round(record.final_score, 3) if record.final_score is not None else None
            })
        
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "records": result_list
        }
    
    except Exception as e:
        logger.error(f"Failed to get compliance history for account {account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get compliance history: {str(e)}")


@router.get("/account/{account_id}/trend")
async def get_compliance_trend(
    account_id: int,
    period: str = Query("day", regex="^(day|week|month)$"),
    metric: str = Query("final_score", regex="^(gate_pass_rate|final_score|s_rule_sat|s_audit)$"),
    db: Session = Depends(get_db)
):
    """
    Get compliance trend data for visualization
    
    Args:
        period: Aggregation period (day/week/month)
        metric: Metric to track (gate_pass_rate, final_score, s_rule_sat, s_audit)
    
    Returns time series data points
    """
    try:
        # Determine time window
        if period == "day":
            days_back = 30
        elif period == "week":
            days_back = 90
        else:  # month
            days_back = 365
        
        start_date = datetime.utcnow() - timedelta(days=days_back)
        
        # Query records in time range
        records = db.query(RuleEvaluationResult).filter(
            and_(
                RuleEvaluationResult.account_id == account_id,
                RuleEvaluationResult.ts >= start_date
            )
        ).order_by(RuleEvaluationResult.ts.asc()).all()
        
        if not records:
            return {
                "period": period,
                "metric": metric,
                "data_points": []
            }
        
        # Group by date and calculate metrics
        from collections import defaultdict
        daily_data = defaultdict(list)
        
        for record in records:
            date_key = record.ts.date().isoformat() if record.ts else None
            if not date_key:
                continue
            
            if metric == "gate_pass_rate":
                daily_data[date_key].append(1 if record.gate_pass == "true" else 0)
            elif metric == "final_score":
                if record.final_score is not None:
                    daily_data[date_key].append(record.final_score)
            elif metric == "s_rule_sat":
                if record.s_rule_sat is not None:
                    daily_data[date_key].append(record.s_rule_sat)
            elif metric == "s_audit":
                if record.s_audit is not None:
                    daily_data[date_key].append(record.s_audit)
        
        # Calculate averages
        data_points = []
        for date_str in sorted(daily_data.keys()):
            values = daily_data[date_str]
            if values:
                avg_value = sum(values) / len(values)
                data_points.append({
                    "date": date_str,
                    "value": round(avg_value, 3),
                    "count": len(values)
                })
        
        return {
            "period": period,
            "metric": metric,
            "data_points": data_points
        }
    
    except Exception as e:
        logger.error(f"Failed to get compliance trend for account {account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get compliance trend: {str(e)}")


@router.get("/account/{account_id}/stats")
async def get_compliance_stats(
    account_id: int,
    db: Session = Depends(get_db)
):
    """
    Get aggregated compliance statistics for an account
    
    Returns:
        - Total evaluations
        - Gate pass rate
        - Average scores (final_score, s_rule_sat, s_audit)
        - Recent performance (last 7 days vs all time)
    """
    try:
        # Get all-time stats
        all_records = db.query(RuleEvaluationResult).filter(
            RuleEvaluationResult.account_id == account_id
        ).all()
        
        if not all_records:
            return {
                "total_evaluations": 0,
                "all_time": None,
                "recent_7d": None,
                "llm_audit_stats": None
            }
        
        # Calculate all-time metrics
        total_count = len(all_records)
        gate_pass_count = sum(1 for r in all_records if r.gate_pass == "true")
        
        final_scores = [r.final_score for r in all_records if r.final_score is not None]
        s_rule_sat_scores = [r.s_rule_sat for r in all_records if r.s_rule_sat is not None]
        s_audit_scores = [r.s_audit for r in all_records if r.s_audit is not None]
        
        all_time_stats = {
            "gate_pass_rate": round(gate_pass_count / total_count, 3) if total_count > 0 else 0,
            "avg_final_score": round(sum(final_scores) / len(final_scores), 3) if final_scores else None,
            "avg_s_rule_sat": round(sum(s_rule_sat_scores) / len(s_rule_sat_scores), 3) if s_rule_sat_scores else None,
            "avg_s_audit": round(sum(s_audit_scores) / len(s_audit_scores), 3) if s_audit_scores else None,
            "evaluation_count": total_count
        }
        
        # Calculate recent 7-day stats
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        recent_records = [r for r in all_records if r.ts and r.ts >= seven_days_ago]
        
        recent_stats = None
        if recent_records:
            recent_count = len(recent_records)
            recent_pass_count = sum(1 for r in recent_records if r.gate_pass == "true")
            
            recent_final = [r.final_score for r in recent_records if r.final_score is not None]
            recent_s_rule = [r.s_rule_sat for r in recent_records if r.s_rule_sat is not None]
            recent_s_audit = [r.s_audit for r in recent_records if r.s_audit is not None]
            
            recent_stats = {
                "gate_pass_rate": round(recent_pass_count / recent_count, 3) if recent_count > 0 else 0,
                "avg_final_score": round(sum(recent_final) / len(recent_final), 3) if recent_final else None,
                "avg_s_rule_sat": round(sum(recent_s_rule) / len(recent_s_rule), 3) if recent_s_rule else None,
                "avg_s_audit": round(sum(recent_s_audit) / len(recent_s_audit), 3) if recent_s_audit else None,
                "evaluation_count": recent_count
            }
        
        # Get LLM audit stats from Account table
        account = db.query(Account).filter(Account.id == account_id).first()
        llm_audit_stats = None
        if account and hasattr(account, 'llm_audit_count') and account.llm_audit_count > 0:
            llm_audit_stats = {
                "count": account.llm_audit_count,
                "avg_score": round(account.llm_audit_avg_score, 3) if account.llm_audit_avg_score else None,
                "avg_coverage": round(account.llm_audit_avg_coverage, 2) if account.llm_audit_avg_coverage else None,
                "avg_conflict": round(account.llm_audit_avg_conflict, 2) if account.llm_audit_avg_conflict else None
            }
        
        return {
            "total_evaluations": total_count,
            "all_time": all_time_stats,
            "recent_7d": recent_stats,
            "llm_audit_stats": llm_audit_stats
        }
    
    except Exception as e:
        logger.error(f"Failed to get compliance stats for account {account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get compliance stats: {str(e)}")


@router.get("/recent-decisions")
async def get_recent_decisions(
    account_id: int = Query(..., description="Account ID"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """
    Get recent trading decisions with compliance scores
    
    Combines AIDecisionLog with RuleEvaluationResult to show decision context
    """
    try:
        # Query recent decisions
        decisions = db.query(AIDecisionLog).filter(
            AIDecisionLog.account_id == account_id
        ).order_by(AIDecisionLog.decision_time.desc()).limit(limit).all()
        
        result_list = []
        for decision in decisions:
            # Find corresponding compliance evaluation
            compliance = None
            if decision.trace_id:
                compliance = db.query(RuleEvaluationResult).filter(
                    RuleEvaluationResult.trace_id == decision.trace_id
                ).first()
            
            result_list.append({
                "trace_id": decision.trace_id,
                "timestamp": decision.decision_time.isoformat() if decision.decision_time else None,
                "operation": decision.operation,
                "symbol": decision.symbol,
                "leverage": decision.leverage,
                "executed": decision.executed == "true",
                "compliance": {
                    "gate_pass": compliance.gate_pass == "true" if compliance else None,
                    "final_score": round(compliance.final_score, 3) if compliance and compliance.final_score else None,
                    "s_audit": round(compliance.s_audit, 3) if compliance and compliance.s_audit else None
                } if compliance else None
            })
        
        return {
            "account_id": account_id,
            "count": len(result_list),
            "decisions": result_list
        }
    
    except Exception as e:
        logger.error(f"Failed to get recent decisions for account {account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get recent decisions: {str(e)}")
