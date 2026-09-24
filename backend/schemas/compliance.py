"""HTTP contracts for rule configuration and compliance reads."""
from pydantic import BaseModel


class ComplianceRecord(BaseModel):
    id: int
    timestamp: str | None
    trace_id: str | None
    gate_pass: bool
    s_rule_sat: float | None
    s_audit: float | None
    final_score: float | None


class ComplianceHistory(BaseModel):
    total: int
    limit: int
    offset: int
    records: list[ComplianceRecord]


class TrendDataPoint(BaseModel):
    date: str
    value: float
    count: int


class ComplianceTrend(BaseModel):
    period: str
    metric: str
    data_points: list[TrendDataPoint]


class ComplianceAggregate(BaseModel):
    gate_pass_rate: float
    avg_final_score: float | None
    avg_s_rule_sat: float | None
    avg_s_audit: float | None
    evaluation_count: int


class LLMAuditStats(BaseModel):
    count: int
    avg_score: float | None
    avg_coverage: float | None
    avg_conflict: float | None


class ComplianceStats(BaseModel):
    total_evaluations: int
    all_time: ComplianceAggregate | None
    recent_7d: ComplianceAggregate | None
    llm_audit_stats: LLMAuditStats | None


class DecisionCompliance(BaseModel):
    gate_pass: bool | None
    final_score: float | None
    s_audit: float | None


class RecentDecision(BaseModel):
    trace_id: str | None
    timestamp: str | None
    operation: str
    symbol: str | None
    leverage: float | None
    executed: bool
    compliance: DecisionCompliance | None


class RecentDecisions(BaseModel):
    account_id: int
    count: int
    decisions: list[RecentDecision]


class RuleCategory(BaseModel):
    name: str
    description: str
    count: int


class RuleCategories(BaseModel):
    r0: RuleCategory
    r1: RuleCategory
    r2: RuleCategory


class RuleSummary(BaseModel):
    total_rules: int
    r0_count: int
    r1_count: int
    r2_count: int
    categories: RuleCategories


class RuleItem(BaseModel):
    id: str
    name: str
    category: str
    category_name: str
    weight: float = 1.0


class RuleList(BaseModel):
    total: int
    rules: list[RuleItem]
