"""
Memory Evaluator V2

Focused on 4 core metrics:
1. Retrieval Distribution - How memories are being retrieved
2. Memory Usage - Memory usage patterns
3. Retrieval Relevance - Quality of search results
4. Memory Growth Pattern - How memory system evolves over time
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import logging
import numpy as np
from collections import Counter

from sqlalchemy.orm import Session
from .base import BaseEvaluator
from .data_loader import EvaluationDataLoader
from database.models import Account

logger = logging.getLogger(__name__)


class MemoryEvaluator(BaseEvaluator):
    """Simplified memory evaluator focused on 4 core metrics."""

    def __init__(self, db: Session):
        self.db = db
        self.data_loader = EvaluationDataLoader(db)

    @property
    def name(self) -> str:
        return "Memory Evaluator V2"

    def evaluate(self, agent_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate memory system with 4 core metrics.

        Args:
            agent_data: Must contain 'account_id', optional 'start_time', 'end_time'

        Returns:
            Evaluation results with 4 core metrics
        """
        account_id = agent_data.get("account_id")
        if not account_id:
            raise ValueError("account_id is required")

        start_time = agent_data.get("start_time")
        end_time = agent_data.get("end_time")

        logger.info(f"Evaluating memory for account {account_id}")

        memories = self.data_loader.get_memories(account_id, start_time, end_time)
        decisions = self.data_loader.get_decisions(account_id, start_time, end_time)
        tool_usage = self.data_loader.get_memory_tool_usage_from_traces(account_id, start_time, end_time)

        return {
            "account_id": account_id,
            "evaluation_time": datetime.now().isoformat(),
            "retrieval_distribution": self._evaluate_retrieval_distribution(memories),
            "memory_diversity": self._evaluate_diversity(memories),
            "memory_usage": self._evaluate_usage(memories, tool_usage),
            "growth_pattern": self._evaluate_growth(memories, tool_usage, decisions)
        }

    def _evaluate_retrieval_distribution(self, memories: List) -> Dict[str, Any]:
        """
        Metric 1: Retrieval Distribution
        Analyzes how memories are being retrieved (zombie vs high-value).
        """
        if not memories:
            return {"total_memories": 0, "zombie_rate": 0, "high_value_rate": 0, "histogram": {}}

        retrieval_counts = [getattr(m, 'retrieval_count', 0) for m in memories]
        zombie_memories = sum(1 for c in retrieval_counts if c == 0)
        high_value_memories = sum(1 for c in retrieval_counts if c >= 5)

        # Histogram: group by retrieval count ranges
        histogram = Counter()
        for count in retrieval_counts:
            if count == 0:
                histogram["0"] += 1
            elif count <= 2:
                histogram["1-2"] += 1
            elif count <= 5:
                histogram["3-5"] += 1
            elif count <= 10:
                histogram["6-10"] += 1
            else:
                histogram["10+"] += 1

        return {
            "total_memories": len(memories),
            "zombie_memories": zombie_memories,
            "zombie_rate": round(zombie_memories / len(memories), 3),
            "high_value_memories": high_value_memories,
            "high_value_rate": round(high_value_memories / len(memories), 3),
            "avg_retrieval_count": round(np.mean(retrieval_counts), 2),
            "median_retrieval_count": int(np.median(retrieval_counts)),
            "histogram": dict(histogram)
        }

    def _evaluate_diversity(self, memories: List) -> Dict[str, Any]:
        """
        Metric 2: Memory Diversity
        Measures semantic diversity using embedding cosine similarity.
        """
        if not memories:
            return {"total_memories": 0, "avg_similarity": 0, "diversity_score": 0}

        embeddings = [m.embedding for m in memories if m.embedding]

        if len(embeddings) < 2:
            return {
                "total_memories": len(memories),
                "embeddings_available": len(embeddings),
                "avg_similarity": 0,
                "diversity_score": 0,
                "note": "Need at least 2 embeddings for diversity analysis"
            }

        # Calculate pairwise cosine similarities
        similarities = self._calculate_pairwise_similarities(embeddings)
        avg_similarity = np.mean(similarities)
        diversity_score = 1 - avg_similarity  # Higher diversity = lower similarity

        return {
            "total_memories": len(memories),
            "embeddings_available": len(embeddings),
            "avg_similarity": round(avg_similarity, 3),
            "diversity_score": round(diversity_score, 3),
            "similarity_std": round(np.std(similarities), 3),
            "interpretation": "High diversity" if diversity_score > 0.5 else "Low diversity"
        }

    def _evaluate_usage(self, memories: List, tool_usage: Dict) -> Dict[str, Any]:
        """
        Metric 3: Memory Usage
        Analyzes memory usage patterns: search frequency, coverage, and recent activity.
        """
        if not memories:
            return {"total_memories": 0, "recently_retrieved": 0, "retrieval_rate": 0}

        search_count = tool_usage.get("memory_search_count", 0)

        # Count memories retrieved in last 24 hours
        now = datetime.now()
        recently_retrieved = sum(
            1 for m in memories
            if getattr(m, 'last_retrieved_at', None) and
            (now - m.last_retrieved_at).total_seconds() < 86400
        )

        # Memories with retrieval_count > 0 (ever retrieved)
        ever_retrieved = sum(1 for m in memories if getattr(m, 'retrieval_count', 0) > 0)

        return {
            "total_memories": len(memories),
            "search_count": search_count,
            "ever_retrieved": ever_retrieved,
            "retrieval_rate": round(ever_retrieved / len(memories), 3),
            "recently_retrieved_24h": recently_retrieved,
            "avg_searches_per_memory": round(search_count / len(memories), 2) if memories else 0
        }

    def _evaluate_growth(self, memories: List, tool_usage: Dict, decisions: List) -> Dict[str, Any]:
        """
        Metric 4: Memory Growth Pattern
        Analyzes how memory system evolves over time.
        """
        if not memories:
            return {"total_memories": 0, "growth_rate": 0, "dedup_rejection_rate": 0}

        add_count = tool_usage.get("memory_add_count", 0)

        # Calculate time span
        if len(memories) > 1:
            timestamps = [m.created_at for m in memories]
            time_span_days = (max(timestamps) - min(timestamps)).days
            growth_rate = len(memories) / max(time_span_days, 1)
        else:
            time_span_days = 0
            growth_rate = 0

        # Dedup rejection rate (add_count > len(memories) means some were rejected)
        dedup_rejection_rate = (add_count - len(memories)) / add_count if add_count > 0 else 0

        # Memory addition rate (memories per decision)
        addition_rate = len(memories) / len(decisions) if decisions else 0

        return {
            "total_memories": len(memories),
            "time_span_days": time_span_days,
            "growth_rate_per_day": round(growth_rate, 2),
            "add_attempts": add_count,
            "dedup_rejections": add_count - len(memories),
            "dedup_rejection_rate": round(dedup_rejection_rate, 3),
            "addition_rate_per_decision": round(addition_rate, 3)
        }

    @staticmethod
    def _calculate_pairwise_similarities(embeddings: List[List[float]]) -> List[float]:
        """Calculate pairwise cosine similarities between embeddings."""
        vectors = np.array(embeddings)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        normalized = vectors / norms

        similarities = []
        n = len(normalized)
        for i in range(n):
            for j in range(i + 1, n):
                sim = np.dot(normalized[i], normalized[j])
                similarities.append(max(0, min(1, sim)))
        return similarities


def main():
    """Run memory evaluation from command line."""
    import sys
    import json
    from database.connection import SessionLocal

    print("=" * 80)
    print("Memory Evaluator V2")
    print("=" * 80)

    db = SessionLocal()
    evaluator = MemoryEvaluator(db)
    data_loader = EvaluationDataLoader(db)

    try:
        # Determine which accounts to evaluate
        if len(sys.argv) > 1:
            account_id = int(sys.argv[1])
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                print(f"Account {account_id} not found")
                return
            accounts = [account]
        else:
            accounts = data_loader.get_agent_accounts()
            if not accounts:
                print("No AI accounts found")
                return

        print(f"\nEvaluating {len(accounts)} account(s)\n")

        results = []
        for account in accounts:
            print("-" * 80)
            print(f"Account: {account.name} (ID: {account.id})")
            print("-" * 80)

            try:
                result = evaluator.evaluate(agent_data={"account_id": account.id})

                # Display results
                print("\n1. RETRIEVAL DISTRIBUTION")
                rd = result['retrieval_distribution']
                print(f"  Total Memories: {rd['total_memories']}")
                print(f"  Zombie Rate: {rd['zombie_rate']:.1%} ({rd['zombie_memories']} memories)")
                print(f"  High-Value Rate: {rd['high_value_rate']:.1%} ({rd['high_value_memories']} memories)")
                print(f"  Avg Retrieval Count: {rd['avg_retrieval_count']}")
                print(f"  Histogram: {rd['histogram']}")

                print("\n2. MEMORY DIVERSITY")
                md = result['memory_diversity']
                print(f"  Total Memories: {md['total_memories']}")
                print(f"  Avg Similarity: {md.get('avg_similarity', 0):.3f}")
                print(f"  Diversity Score: {md.get('diversity_score', 0):.3f}")
                print(f"  Interpretation: {md.get('interpretation', 'N/A')}")

                print("\n3. RETRIEVAL RELEVANCE")
                rr = result['retrieval_relevance']
                print(f"  Total Searches: {rr['search_count']}")
                print(f"  Ever Retrieved: {rr['ever_retrieved']} ({rr['retrieval_rate']:.1%})")
                print(f"  Recently Retrieved (24h): {rr['recently_retrieved_24h']}")

                print("\n4. GROWTH PATTERN")
                gp = result['growth_pattern']
                print(f"  Time Span: {gp['time_span_days']} days")
                print(f"  Growth Rate: {gp['growth_rate_per_day']:.2f} memories/day")
                print(f"  Dedup Rejections: {gp['dedup_rejections']} ({gp['dedup_rejection_rate']:.1%})")
                print(f"  Addition Rate: {gp['addition_rate_per_decision']:.3f} memories/decision")
                print()

                results.append({
                    "account_id": account.id,
                    "account_name": account.name,
                    "result": result
                })

            except Exception as e:
                print(f"Error: {e}")
                import traceback
                traceback.print_exc()

        # Save results
        output_file = "memory_evaluation_results.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nResults saved to: {output_file}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
