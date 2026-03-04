from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class MemoryOut(BaseModel):
    id: int
    content: str
    created_at: datetime
    retrieval_count: int
    last_retrieved_at: Optional[datetime]


class MemoryMetrics(BaseModel):
    account_id: int
    evaluation_time: str
    retrieval_distribution: dict
    memory_diversity: dict
    retrieval_relevance: dict
    growth_pattern: dict


class GrowthTimeline(BaseModel):
    timeline: List[dict]
