"""HTTP read contracts, including optional fields of empty historical results."""

from datetime import datetime
from pydantic import BaseModel


class MemoryItem(BaseModel):
    id: int
    content: str
    market: str | None
    created_at: datetime
    retrieval_count: int
    last_retrieved_at: datetime | None


class MemoryList(BaseModel):
    memories: list[MemoryItem]


class MemoryRetrieval(BaseModel):
    total_memories: int
    zombie_rate: float
    high_value_rate: float
    recently_retrieved_24h: int
    histogram: dict[str, int]
    zombie_memories: int | None = None
    high_value_memories: int | None = None
    avg_retrieval_count: float | None = None
    median_retrieval_count: int | None = None


class MemoryDiversity(BaseModel):
    total_memories: int
    avg_similarity: float
    diversity_score: float
    embeddings_available: int | None = None
    note: str | None = None
    similarity_std: float | None = None
    interpretation: str | None = None


class MemoryGrowth(BaseModel):
    total_memories: int
    dedup_rejection_rate: float
    growth_rate: float | None = None
    time_span_days: int | None = None
    growth_rate_per_day: float | None = None
    add_attempts: int | None = None
    dedup_rejections: int | None = None
    addition_rate_per_decision: float | None = None


class MemoryMetrics(BaseModel):
    account_id: int
    evaluation_time: datetime
    retrieval_distribution: MemoryRetrieval
    memory_diversity: MemoryDiversity
    growth_pattern: MemoryGrowth


class MemoryTimelinePoint(BaseModel):
    date: str
    cumulative_count: int
    daily_additions: int


class MemoryTimeline(BaseModel):
    timeline: list[MemoryTimelinePoint]


class DeleteMemoriesResult(BaseModel):
    success: bool
    deleted: int


class FactorColumn(BaseModel):
    key: str
    label: str
    type: str
    sortable: bool


class FactorDefinition(BaseModel):
    id: str
    name: str
    description: str
    columns: list[FactorColumn]


class RankingFactors(BaseModel):
    success: bool
    factors: list[FactorDefinition]
    all_columns: list[FactorColumn]


class RankingTable(BaseModel):
    success: bool
    data: list[dict[str, str | float | bool | None]]
    message: str | None = None
    total_symbols: int | None = None
    data_period: str | None = None
    factors_computed: list[str] | str | None = None


class RankingSymbols(BaseModel):
    success: bool
    symbols: list[str]
    count: int
    data_period: str


class OverviewAccount(BaseModel):
    id: int
    name: str
    account_type: str
    agent_type: str | None
    current_cash: float
    frozen_cash: float


class OverviewTotals(BaseModel):
    total_assets: float
    positions_value: float
    positions_count: int
    pending_orders: int


class AuditStats(BaseModel):
    count: int
    avg_score: float | None
    avg_coverage: float | None
    avg_conflict: float | None


class AccountOverview(OverviewTotals):
    account: OverviewAccount
    llm_audit_stats: AuditStats | None


class DefaultOverview(BaseModel):
    account: OverviewAccount
    portfolio: OverviewTotals


class LatestTrace(BaseModel):
    trace_id: str | None


class ManualDecisionResponse(BaseModel):
    decision_round_id: str | None
    processed_accounts: int
    errors: dict[int, str]


class LLMConnectionResult(BaseModel):
    success: bool
    message: str
    response: str | None = None
    normalized_base_url: str | None = None


class HealthResponse(BaseModel):
    status: str
    message: str
    services: dict[str, str] | None = None


class ReadinessResponse(BaseModel):
    ready: bool
    status: str
    services: dict[str, str]
    dependencies: dict[str, str]


class HealthPrice(BaseModel):
    symbol: str
    price: float | None


class MarketHealth(BaseModel):
    status: str
    timestamp: int
    message: str
    test_price: HealthPrice | None = None
    error: str | None = None


class CryptoPrice(BaseModel):
    symbol: str
    price: float
    market: str


class PopularCrypto(CryptoPrice):
    name: str
