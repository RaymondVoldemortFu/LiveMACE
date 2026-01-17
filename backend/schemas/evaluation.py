"""Rule evaluation-related Pydantic schemas for API validation"""

from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class RuleViolation(BaseModel):
    """Single rule violation detail"""
    rule: str
    level: str
    severity: str
    description: str
    value: Optional[float | str] = None
    threshold: Optional[float | str] = None


class RuleEvaluationResultOut(BaseModel):
    """Rule evaluation result output"""
    id: int
    trace_id: str
    account_id: int
    ts: datetime
    gate_pass: str  # "true" or "false"
    r0_violations_json: Optional[str] = None
    r1_violations_json: Optional[str] = None
    r2_scores_json: Optional[str] = None
    s_rule_sat: float
    s_audit: Optional[float] = None
    final_score: float

    class Config:
        from_attributes = True


class RuleEvaluationDetail(BaseModel):
    """Detailed evaluation with parsed violations"""
    id: int
    trace_id: str
    account_id: int
    ts: datetime
    gate_pass: bool
    r0_violations: list[RuleViolation]
    r1_violations: list[RuleViolation]
    r2_scores: dict[str, float]
    s_rule_sat: float
    s_audit: Optional[float] = None
    final_score: float


class EvaluationStatistics(BaseModel):
    """Evaluation statistics for an account"""
    account_id: int
    account_name: str
    total_evaluations: int
    gate_pass_rate: float
    avg_final_score: float
    avg_s_rule_sat: float
    avg_s_audit: Optional[float] = None
    violation_counts: dict[str, int]  # rule_id -> count
    time_range: dict[str, datetime]  # start_time, end_time


class EvaluationQuery(BaseModel):
    """Query parameters for evaluation results"""
    account_id: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    gate_pass: Optional[bool] = None
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    limit: int = 100


class BatchEvaluationRequest(BaseModel):
    """Request for batch evaluation"""
    account_id: Optional[int] = None  # If None, evaluate all accounts
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    run_llm_audit: bool = False  # Whether to run expensive LLM audit
