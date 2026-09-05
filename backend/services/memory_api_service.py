import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy.orm import Session

from database.models import AgentMemory
from services.agent.memory import get_memory_service
from services.evaluation.memory_evaluator import MemoryEvaluator

logger = logging.getLogger(__name__)


class MemoryApiService:
    def __init__(self, db: Session):
        self.db = db

    def _memory_query(self, account_id: int, market: Optional[str]):
        query = self.db.query(AgentMemory).filter(AgentMemory.account_id == account_id)
        return query.filter(AgentMemory.market == market) if market else query

    def list_memories(self, account_id: int, market: Optional[str]):
        memories = self._memory_query(account_id, market).order_by(AgentMemory.created_at.desc()).all()
        return {
            "memories": [
                {
                    "id": memory.id,
                    "content": memory.content,
                    "market": memory.market,
                    "created_at": memory.created_at,
                    "retrieval_count": memory.retrieval_count or 0,
                    "last_retrieved_at": memory.last_retrieved_at,
                }
                for memory in memories
            ]
        }

    def get_metrics(self, account_id: int, market: Optional[str]):
        agent_data = {"account_id": account_id}
        if market:
            agent_data["market"] = market
        return MemoryEvaluator(self.db).evaluate(agent_data=agent_data)

    def get_growth_timeline(self, account_id: int, market: Optional[str]):
        memories = self._memory_query(account_id, market).order_by(AgentMemory.created_at).all()
        daily_counts = defaultdict(int)
        for memory in memories:
            daily_counts[memory.created_at.date().isoformat()] += 1
        cumulative = 0
        timeline = []
        for date in sorted(daily_counts):
            cumulative += daily_counts[date]
            timeline.append(
                {"date": date, "cumulative_count": cumulative, "daily_additions": daily_counts[date]}
            )
        return {"timeline": timeline}

    def clear_all(self):
        try:
            count = self.db.query(AgentMemory).count()
            self.db.query(AgentMemory).delete()
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        try:
            memory_backend = get_memory_service()
            if memory_backend:
                memory_backend.reset()
        except Exception as exc:
            logger.warning("Could not reset vector backend: %s", exc)
        return {"success": True, "deleted": count}

    def clear_account(self, account_id: int):
        try:
            query = self.db.query(AgentMemory).filter(AgentMemory.account_id == account_id)
            count = query.count()
            query.delete()
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        try:
            memory_backend = get_memory_service()
            if memory_backend:
                memory_backend.clear_account_memories(str(account_id))
        except Exception as exc:
            logger.warning("Could not clear vector backend memories: %s", exc)
        return {"success": True, "deleted": count}
