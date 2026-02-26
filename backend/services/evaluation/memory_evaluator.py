"""
Memory Evaluator

Core Principles:
1. Memory value is measured by actual usage and impact
2. Effectiveness is measured by decision outcomes
3. Temporal relevance is measured by continued usage over time
4. Uniqueness is measured by information diversity
"""

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import json
import logging
from collections import defaultdict

from sqlalchemy.orm import Session
from .base import BaseEvaluator
from .data_loader import EvaluationDataLoader
from database.models import AgentTrace, Account

logger = logging.getLogger(__name__)


class MemoryEvaluator(BaseEvaluator):
    """
    Result-oriented memory capability evaluator.
    """

    def __init__(self, db: Session):
        self.db = db
        self.data_loader = EvaluationDataLoader(db)

    @property
    def name(self) -> str:
        return "Memory Evaluator"

    def evaluate(self, agent_data: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate agent's memory capabilities based on actual utility.

        Args:
            agent_data: Must contain 'account_id', optional 'start_time', 'end_time'
            market_data: Market context data (optional)

        Returns:
            Comprehensive memory evaluation metrics
        """
        account_id = agent_data.get("account_id")
        if not account_id:
            raise ValueError("account_id is required in agent_data")

        start_time = agent_data.get("start_time")
        end_time = agent_data.get("end_time")

        logger.info(f"Evaluating memory capabilities for account {account_id}")

        # Collect all evaluation metrics
        results = {
            "account_id": account_id,
            "evaluation_period": {
                "start": start_time.isoformat() if start_time else None,
                "end": end_time.isoformat() if end_time else None
            },
            "usage_metrics": self._evaluate_usage(account_id, start_time, end_time),
            "effectiveness_metrics": self._evaluate_effectiveness(account_id, start_time, end_time),
            "temporal_metrics": self._evaluate_temporal_relevance(account_id, start_time, end_time),
            "diversity_metrics": self._evaluate_diversity(account_id, start_time, end_time),
            "overall_score": 0.0
        }

        # Calculate overall score
        results["overall_score"] = self._calculate_overall_score(results)

        return results

    def _evaluate_usage(self, account_id: int, start_time: Optional[datetime], end_time: Optional[datetime]) -> Dict[str, Any]:
        """
        Evaluate memory usage patterns.

        Key Insight: Good memory system should be actively used.

        Metrics:
        - Usage rate: How often memories are retrieved relative to decisions
        - Retrieval-to-storage ratio: Balance between reading and writing
        - Active memory ratio: Percentage of memories that have been retrieved at least once
        """
        memories = self.data_loader.get_memories(account_id, start_time, end_time)
        decisions = self.data_loader.get_decisions(account_id, start_time, end_time)
        tool_usage = self.data_loader.get_memory_tool_usage_from_traces(account_id, start_time, end_time)

        total_memories = len(memories)
        total_decisions = len(decisions)
        search_count = tool_usage["memory_search_count"]
        add_count = tool_usage["memory_add_count"]

        # Usage rate: searches per decision
        usage_rate = search_count / total_decisions if total_decisions > 0 else 0

        # Retrieval-to-storage ratio (ideal: 2-5, meaning memories are reused)
        retrieval_storage_ratio = search_count / add_count if add_count > 0 else 0

        # Active memory ratio: now using actual retrieval_count from database
        active_memories = sum(1 for m in memories if getattr(m, 'retrieval_count', 0) > 0)
        active_ratio = active_memories / total_memories if total_memories > 0 else 0

        # Total retrieval count across all memories
        total_retrievals = sum(getattr(m, 'retrieval_count', 0) for m in memories)
        avg_retrievals = total_retrievals / total_memories if total_memories > 0 else 0

        # Calculate usage score (0-100)
        usage_score = self._calculate_usage_score(usage_rate, retrieval_storage_ratio, active_ratio)

        return {
            "total_memories": total_memories,
            "total_decisions": total_decisions,
            "search_count": search_count,
            "add_count": add_count,
            "usage_rate": round(usage_rate, 2),
            "retrieval_storage_ratio": round(retrieval_storage_ratio, 2),
            "active_memory_ratio": round(active_ratio, 2),
            "active_memories": active_memories,
            "total_retrievals": total_retrievals,
            "avg_retrievals_per_memory": round(avg_retrievals, 2),
            "usage_score": usage_score,
            "interpretation": self._interpret_usage(usage_rate, retrieval_storage_ratio)
        }

    def _evaluate_effectiveness(self, account_id: int, start_time: Optional[datetime], end_time: Optional[datetime]) -> Dict[str, Any]:
        """
        Evaluate memory effectiveness by comparing outcomes.

        Key Insight: Good memories should lead to better decisions.

        Approach:
        1. Compare trades made WITH memory retrieval vs WITHOUT
        2. Measure trade value differences
        3. Measure activity patterns
        """
        trades = self.data_loader.get_trades(account_id, start_time, end_time)
        traces = self.data_loader.get_traces(account_id, start_time, end_time)
        decisions = self.data_loader.get_decisions(account_id, start_time, end_time)

        # Group traces by decision session (trace_id)
        decision_sessions = self._group_traces_by_session(traces)

        # Identify which decisions used memory
        decisions_with_memory = set()
        decisions_without_memory = set()
        decision_times = {}  # trace_id -> decision_time

        for trace_id, trace_list in decision_sessions.items():
            used_memory = any(
                self._is_memory_tool_call(trace) for trace in trace_list
            )
            # Get decision time from first trace in session
            if trace_list:
                decision_times[trace_id] = trace_list[0].created_at

            if used_memory:
                decisions_with_memory.add(trace_id)
            else:
                decisions_without_memory.add(trace_id)

        # Also check AIDecisionLog for additional decision info
        for decision in decisions:
            decision_time = decision.decision_time
            # Try to match to trace sessions by time
            matched = False
            for trace_id, trace_time in decision_times.items():
                # If decision time is within 1 minute of trace time, consider them related
                if abs((decision_time - trace_time).total_seconds()) < 60:
                    matched = True
                    break

            # If no trace session found, create a pseudo session
            if not matched:
                pseudo_trace_id = f"decision_{decision.id}"
                decision_times[pseudo_trace_id] = decision_time
                # Assume no memory usage if no trace found
                decisions_without_memory.add(pseudo_trace_id)

        # Match trades to decisions by timestamp
        # Trade is associated with the nearest decision within 5 minutes
        trades_with_memory = []
        trades_without_memory = []

        for trade in trades:
            trade_time = trade.trade_time
            nearest_decision = None
            min_time_diff = float('inf')

            # Find nearest decision
            for trace_id, decision_time in decision_times.items():
                time_diff = abs((trade_time - decision_time).total_seconds())
                if time_diff < min_time_diff and time_diff < 300:  # Within 5 minutes
                    min_time_diff = time_diff
                    nearest_decision = trace_id

            # Categorize trade based on nearest decision
            if nearest_decision:
                if nearest_decision in decisions_with_memory:
                    trades_with_memory.append(trade)
                elif nearest_decision in decisions_without_memory:
                    trades_without_memory.append(trade)

        # Calculate effectiveness metrics
        effectiveness_score, comparison = self._compare_trade_outcomes(
            trades_with_memory, trades_without_memory
        )

        return {
            "decisions_with_memory": len(decisions_with_memory),
            "decisions_without_memory": len(decisions_without_memory),
            "trades_with_memory": len(trades_with_memory),
            "trades_without_memory": len(trades_without_memory),
            "effectiveness_score": effectiveness_score,
            "outcome_comparison": comparison,
            "matching_method": "timestamp-based (5 min window)"
        }

    def _evaluate_temporal_relevance(self, account_id: int, start_time: Optional[datetime], end_time: Optional[datetime]) -> Dict[str, Any]:
        """
        Evaluate temporal relevance of memories.

        Key Insight: Good memories remain useful over time.

        Metrics:
        - Memory lifespan: How long memories remain in the system
        - Reuse rate: How often old memories are still retrieved
        - Decay pattern: Whether memory usage decreases over time
        """
        memories = self.data_loader.get_memories(account_id, start_time, end_time)

        if not memories:
            return {
                "total_memories": 0,
                "avg_memory_age_days": 0,
                "oldest_memory_days": 0,
                "newest_memory_days": 0,
                "temporal_score": 0,
                "interpretation": "No memories to analyze"
            }

        # Calculate memory ages
        now = datetime.now()
        memory_ages = [(now - m.created_at).days for m in memories]
        avg_age = sum(memory_ages) / len(memory_ages) if memory_ages else 0

        # Temporal score: balance between having history and staying current
        # Ideal: 7-30 days (shows both accumulation and relevance)
        temporal_score = self._calculate_temporal_score(avg_age)

        return {
            "total_memories": len(memories),
            "avg_memory_age_days": round(avg_age, 1),
            "oldest_memory_days": max(memory_ages) if memory_ages else 0,
            "newest_memory_days": min(memory_ages) if memory_ages else 0,
            "temporal_score": temporal_score,
            "interpretation": self._interpret_temporal(avg_age)
        }

    def _evaluate_diversity(self, account_id: int, start_time: Optional[datetime], end_time: Optional[datetime]) -> Dict[str, Any]:
        """
        Evaluate memory diversity and information richness.

        Key Insight: Good memory system should capture diverse experiences.

        Metrics:
        - Semantic diversity: Based on cosine similarity of embeddings (primary metric)
        - Vocabulary diversity: Unique words / total words (fallback)
        - Temporal distribution: Memories spread across time periods
        """
        memories = self.data_loader.get_memories(account_id, start_time, end_time)

        if not memories:
            return {
                "total_memories": 0,
                "unique_words": 0,
                "total_words": 0,
                "vocab_diversity": 0,
                "semantic_diversity": 0,
                "avg_cosine_similarity": 0,
                "temporal_distribution": 0,
                "diversity_score": 0,
                "diversity_method": "none",
                "interpretation": "No memories to analyze"
            }

        # Try to use semantic diversity based on embeddings (preferred method)
        embeddings = []
        for memory in memories:
            if memory.embedding:
                embeddings.append(memory.embedding)

        semantic_diversity = 0
        avg_cosine_similarity = 0
        diversity_method = "vocabulary"  # Default fallback method

        if len(embeddings) >= 2:
            # Calculate pairwise cosine similarities
            similarities = self._calculate_pairwise_cosine_similarities(embeddings)
            avg_cosine_similarity = sum(similarities) / len(similarities) if similarities else 0
            # Semantic diversity: 1 - similarity (higher diversity = lower similarity)
            semantic_diversity = 1 - avg_cosine_similarity
            diversity_method = "embedding"

        # Calculate vocabulary diversity (fallback or supplementary metric)
        all_words = []
        for memory in memories:
            words = memory.content.lower().split()
            all_words.extend(words)

        unique_words = len(set(all_words))
        total_words = len(all_words)
        vocab_diversity = unique_words / total_words if total_words > 0 else 0

        # Calculate temporal distribution (how spread out memories are)
        if len(memories) > 1:
            timestamps = [m.created_at for m in memories]
            time_span = (max(timestamps) - min(timestamps)).total_seconds()
            avg_gap = time_span / (len(memories) - 1) if len(memories) > 1 else 0
            temporal_distribution = min(avg_gap / 3600, 24) / 24  # Normalize to 0-1 (ideal: ~24h gaps)
        else:
            temporal_distribution = 0

        # Calculate diversity score based on method used
        if diversity_method == "embedding":
            # Prefer semantic diversity from embeddings
            diversity_score = (semantic_diversity * 0.7 + temporal_distribution * 0.3) * 100
        else:
            # Fallback to vocabulary diversity
            diversity_score = (vocab_diversity * 0.6 + temporal_distribution * 0.4) * 100

        return {
            "total_memories": len(memories),
            "unique_words": unique_words,
            "total_words": total_words,
            "vocab_diversity": round(vocab_diversity, 3),
            "semantic_diversity": round(semantic_diversity, 3),
            "avg_cosine_similarity": round(avg_cosine_similarity, 3),
            "temporal_distribution": round(temporal_distribution, 3),
            "diversity_score": round(diversity_score, 2),
            "diversity_method": diversity_method,
            "interpretation": self._interpret_diversity(semantic_diversity if diversity_method == "embedding" else vocab_diversity, temporal_distribution)
        }

    # ========== Helper Methods ==========

    @staticmethod
    def _calculate_pairwise_cosine_similarities(embeddings: List[List[float]]) -> List[float]:
        """
        Calculate pairwise cosine similarities between all embedding vectors.

        Args:
            embeddings: List of embedding vectors (each is a list of floats)

        Returns:
            List of cosine similarity values (0-1, where 1 = identical, 0 = orthogonal)
        """
        import numpy as np

        if len(embeddings) < 2:
            return []

        # Convert to numpy arrays
        vectors = np.array(embeddings)

        # Normalize vectors
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1  # Avoid division by zero
        normalized_vectors = vectors / norms

        # Calculate pairwise similarities
        similarities = []
        n = len(normalized_vectors)
        for i in range(n):
            for j in range(i + 1, n):
                similarity = np.dot(normalized_vectors[i], normalized_vectors[j])
                # Clip to [0, 1] range (handle numerical errors)
                similarity = max(0, min(1, similarity))
                similarities.append(similarity)

        return similarities

    @staticmethod
    def _calculate_usage_score(usage_rate: float, retrieval_storage_ratio: float, active_ratio: float) -> float:
        """
        Calculate usage score based on multiple factors.

        Logic:
        - High usage rate (>0.5) is good: shows memories are consulted
        - Retrieval-storage ratio 2-5 is ideal: shows reuse without over-querying
        - High active ratio is good: shows diverse memory usage
        """
        # Usage rate score (0-40 points)
        usage_rate_score = min(usage_rate * 80, 40)

        # Retrieval-storage ratio score (0-40 points)
        # Ideal range: 2-5
        if 2 <= retrieval_storage_ratio <= 5:
            ratio_score = 40
        elif retrieval_storage_ratio > 5:
            ratio_score = max(40 - (retrieval_storage_ratio - 5) * 5, 20)
        else:
            ratio_score = retrieval_storage_ratio * 20

        # Active ratio score (0-20 points)
        active_score = active_ratio * 20

        return round(usage_rate_score + ratio_score + active_score, 2)

    @staticmethod
    def _compare_trade_outcomes(trades_with_memory: List, trades_without_memory: List) -> Tuple[float, Dict]:
        """
        Compare trading outcomes between memory-assisted and non-memory trades.

        Note: Trade model doesn't have a direct 'pnl' field.
        We calculate trade value as: (quantity * price) for sells, -(quantity * price) for buys
        This gives a rough indicator of trading activity, not actual P&L.

        For accurate P&L, we'd need to match buy/sell pairs, which is complex.
        For now, we compare trade volumes and counts as a proxy.

        Returns: (effectiveness_score, comparison_dict)
        """
        if not trades_with_memory and not trades_without_memory:
            return 50.0, {"note": "No trades to compare"}

        # Calculate trade values (not true PnL, but trade volume/activity)
        # Buy trades are negative (cash outflow), sell trades are positive (cash inflow)
        def calc_trade_value(trade):
            value = float(trade.quantity) * float(trade.price)
            # Subtract fees
            value -= float(trade.commission) + float(trade.taker_fee)
            # If it's a buy, make it negative
            if trade.side.upper() == 'BUY':
                value = -value
            return value

        values_with = [calc_trade_value(t) for t in trades_with_memory]
        values_without = [calc_trade_value(t) for t in trades_without_memory]

        avg_value_with = sum(values_with) / len(values_with) if values_with else 0
        avg_value_without = sum(values_without) / len(values_without) if values_without else 0

        # Calculate "success" as net positive cash flow (more sells than buys)
        success_with = sum(1 for v in values_with if v > 0)
        success_without = sum(1 for v in values_without if v > 0)

        success_rate_with = success_with / len(trades_with_memory) if trades_with_memory else 0
        success_rate_without = success_without / len(trades_without_memory) if trades_without_memory else 0

        # Calculate effectiveness score
        # Note: This is a rough heuristic, not true P&L comparison
        if avg_value_without != 0:
            value_improvement = (avg_value_with - avg_value_without) / abs(avg_value_without)
        else:
            value_improvement = 0 if avg_value_with == 0 else (1 if avg_value_with > 0 else -1)

        effectiveness_score = 50 + (value_improvement * 50)
        effectiveness_score = max(0, min(100, effectiveness_score))

        comparison = {
            "avg_trade_value_with_memory": round(avg_value_with, 2),
            "avg_trade_value_without_memory": round(avg_value_without, 2),
            "sell_rate_with_memory": round(success_rate_with, 3),
            "sell_rate_without_memory": round(success_rate_without, 3),
            "value_improvement_pct": round(value_improvement * 100, 2),
            "note": "Trade values are rough estimates, not true P&L. True P&L requires matching buy/sell pairs."
        }

        return round(effectiveness_score, 2), comparison

    @staticmethod
    def _calculate_temporal_score(avg_age_days: float) -> float:
        """
        Calculate temporal score based on average memory age.

        Logic: Balance between accumulation and freshness
        - 7-30 days: Ideal range (80-100 points)
        - 0-7 days: Too new, not enough history (40-80 points)
        - 30+ days: Getting stale (decreasing from 80)
        """
        if 7 <= avg_age_days <= 30:
            return 80 + (30 - abs(avg_age_days - 18.5)) / 11.5 * 20
        elif avg_age_days < 7:
            return 40 + (avg_age_days / 7) * 40
        else:
            return max(80 - (avg_age_days - 30) * 2, 20)

    @staticmethod
    def _group_traces_by_session(traces: List[AgentTrace]) -> Dict[str, List[AgentTrace]]:
        """Group traces by trace_id (decision session)."""
        sessions = defaultdict(list)
        for trace in traces:
            if trace.trace_id:
                sessions[trace.trace_id].append(trace)
        return dict(sessions)

    @staticmethod
    def _is_memory_tool_call(trace: AgentTrace) -> bool:
        """Check if a trace represents a memory tool call."""
        if trace.role != "assistant" or not trace.tool_calls:
            return False
        try:
            import ast
            # tool_calls is a JSON string, need to parse it
            tool_calls_list = json.loads(trace.tool_calls)
            if isinstance(tool_calls_list, list):
                for tool_call in tool_calls_list:
                    if isinstance(tool_call, str):
                        # Inner element is a Python dict string, use ast.literal_eval
                        tool_call = ast.literal_eval(tool_call)
                    tool_name = tool_call.get("function", {}).get("name")
                    if tool_name in ["memory_add", "memory_search"]:
                        return True
            return False
        except:
            return False

    @staticmethod
    def _interpret_usage(usage_rate: float, retrieval_storage_ratio: float) -> str:
        """Provide human-readable interpretation of usage patterns."""
        if usage_rate < 0.2:
            return "Low usage: Agent rarely consults memories"
        elif usage_rate < 0.5:
            return "Moderate usage: Agent occasionally consults memories"
        else:
            if retrieval_storage_ratio < 2:
                return "High usage but low reuse: Agent stores more than it retrieves"
            elif retrieval_storage_ratio > 5:
                return "High usage with high reuse: Agent frequently reuses memories"
            else:
                return "Optimal usage: Good balance between storage and retrieval"

    @staticmethod
    def _interpret_temporal(avg_age_days: float) -> str:
        """Provide human-readable interpretation of temporal patterns."""
        if avg_age_days < 3:
            return "Very fresh: Memory system just started, limited history"
        elif avg_age_days < 7:
            return "Fresh: Building up memory base"
        elif avg_age_days <= 30:
            return "Mature: Good balance of history and freshness"
        else:
            return "Aging: Consider refreshing or pruning old memories"

    @staticmethod
    def _interpret_diversity(vocab_diversity: float, temporal_distribution: float) -> str:
        """Provide human-readable interpretation of diversity."""
        if vocab_diversity < 0.3:
            return "Low diversity: Memories are repetitive"
        elif vocab_diversity < 0.5:
            return "Moderate diversity: Some variety in memories"
        else:
            return "High diversity: Rich and varied memory content"

    @staticmethod
    def _calculate_overall_score(results: Dict[str, Any]) -> float:
        """
        Calculate overall memory capability score (0-100).

        Weights based on importance:
        - Effectiveness: 40% (most important - does memory actually help?)
        - Usage: 30% (is memory being used?)
        - Diversity: 15% (is memory content rich?)
        - Temporal: 15% (is memory staying relevant?)
        """
        weights = {
            "effectiveness": 0.40,
            "usage": 0.30,
            "diversity": 0.15,
            "temporal": 0.15
        }

        effectiveness_score = results["effectiveness_metrics"].get("effectiveness_score", 50)
        usage_score = results["usage_metrics"].get("usage_score", 0)
        diversity_score = results["diversity_metrics"].get("diversity_score", 0)
        temporal_score = results["temporal_metrics"].get("temporal_score", 0)

        overall = (
            effectiveness_score * weights["effectiveness"] +
            usage_score * weights["usage"] +
            diversity_score * weights["diversity"] +
            temporal_score * weights["temporal"]
        )

        return round(overall, 2)


def main():
    """
    Main function to run memory evaluation from command line.

    Usage:
        python memory_evaluator.py [account_id]

    Examples:
        python memory_evaluator.py           # Evaluate all AI accounts
        python memory_evaluator.py 1         # Evaluate account ID 1
    """
    import sys
    import json

    from database.connection import SessionLocal

    print("=" * 80)
    print("Memory Evaluator")
    print("=" * 80)

    db = SessionLocal()
    evaluator = MemoryEvaluator(db)
    data_loader = EvaluationDataLoader(db)

    try:
        # Determine which accounts to evaluate
        if len(sys.argv) > 1:
            # Evaluate specific account
            account_id = int(sys.argv[1])
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account:
                print(f"Account {account_id} not found")
                return
            accounts = [account]
        else:
            # Evaluate all AI accounts
            accounts = data_loader.get_agent_accounts()
            if not accounts:
                print("No AI accounts found in database")
                return

        print(f"\nFound {len(accounts)} account(s) to evaluate\n")

        # Evaluate each account
        results = []
        for account in accounts:
            print("-" * 80)
            print(f"Evaluating Account: {account.name} (ID: {account.id})")
            print(f"  Agent Type: {account.agent_type}")
            print(f"  Model: {account.model}")
            print("-" * 80)

            try:
                # Run evaluation
                result = evaluator.evaluate(
                    agent_data={"account_id": account.id},
                    market_data={}
                )

                # Display results
                print("\nEVALUATION RESULTS\n")

                # Overall Score
                print(f"Overall Score: {result['overall_score']:.2f}/100")
                print()

                # Usage Metrics
                print("Usage Metrics:")
                usage = result['usage_metrics']
                print(f"  • Total Memories: {usage['total_memories']}")
                print(f"  • Total Decisions: {usage['total_decisions']}")
                print(f"  • Memory Searches: {usage['search_count']}")
                print(f"  • Memory Additions: {usage['add_count']}")
                print(f"  • Usage Rate: {usage['usage_rate']:.2f} (searches per decision)")
                print(f"  • Retrieval/Storage Ratio: {usage['retrieval_storage_ratio']:.2f}")
                print(f"  • Active Memory Ratio: {usage['active_memory_ratio']:.2f}")
                print(f"  • Usage Score: {usage['usage_score']:.2f}/100")
                print(f"  • Interpretation: {usage['interpretation']}")
                print()

                # Effectiveness Metrics
                print("Effectiveness Metrics:")
                eff = result['effectiveness_metrics']
                print(f"  • Decisions with Memory: {eff['decisions_with_memory']}")
                print(f"  • Decisions without Memory: {eff['decisions_without_memory']}")
                print(f"  • Trades with Memory: {eff['trades_with_memory']}")
                print(f"  • Trades without Memory: {eff['trades_without_memory']}")
                print(f"  • Effectiveness Score: {eff['effectiveness_score']:.2f}/100")
                print(f"  • Matching Method: {eff['matching_method']}")

                comparison = eff['outcome_comparison']
                if 'note' not in comparison:
                    print(f"  • Avg Trade Value (with memory): ${comparison.get('avg_trade_value_with_memory', 0):.2f}")
                    print(f"  • Avg Trade Value (without memory): ${comparison.get('avg_trade_value_without_memory', 0):.2f}")
                    print(f"  • Value Improvement: {comparison.get('value_improvement_pct', 0):.2f}%")
                else:
                    print(f"  • Note: {comparison['note']}")
                print()

                # Temporal Metrics
                print("Temporal Metrics:")
                temporal = result['temporal_metrics']
                print(f"  • Total Memories: {temporal['total_memories']}")
                print(f"  • Average Memory Age: {temporal['avg_memory_age_days']:.1f} days")
                print(f"  • Oldest Memory: {temporal['oldest_memory_days']} days")
                print(f"  • Newest Memory: {temporal['newest_memory_days']} days")
                print(f"  • Temporal Score: {temporal['temporal_score']:.2f}/100")
                print(f"  • Interpretation: {temporal['interpretation']}")
                print()

                # Diversity Metrics
                print("Diversity Metrics:")
                diversity = result['diversity_metrics']
                print(f"  • Total Memories: {diversity['total_memories']}")
                print(f"  • Unique Words: {diversity['unique_words']}")
                print(f"  • Total Words: {diversity['total_words']}")
                print(f"  • Vocabulary Diversity: {diversity['vocab_diversity']:.3f}")
                print(f"  • Temporal Distribution: {diversity['temporal_distribution']:.3f}")
                print(f"  • Diversity Score: {diversity['diversity_score']:.2f}/100")
                print(f"  • Interpretation: {diversity['interpretation']}")
                print()

                results.append({
                    "account_id": account.id,
                    "account_name": account.name,
                    "result": result
                })

            except Exception as e:
                print(f"Error evaluating account {account.id}: {e}")
                import traceback
                traceback.print_exc()

        # Summary
        if len(results) > 1:
            print("\n" + "=" * 80)
            print("SUMMARY")
            print("=" * 80)
            for r in results:
                score = r['result']['overall_score']
                print(f"  {r['account_name']} (ID {r['account_id']}): {score:.2f}/100")

        # Save results to file
        output_file = "memory_evaluation_results.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nResults saved to: {output_file}")

    finally:
        db.close()


if __name__ == "__main__":
    main()

