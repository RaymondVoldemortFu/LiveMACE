"""
Local memory system using sentence-transformers.
No external services required - all embeddings and storage are local.
"""
import logging
import json
import uuid
import math
import numpy as np
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from config.agent_config import AgentConfig
from database.models import AgentMemory
from database.connection import SessionLocal

logger = logging.getLogger(__name__)

# Try importing sentence-transformers
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    logger.warning("sentence-transformers not installed. Install with: pip install sentence-transformers")


class MemoryInterface(ABC):
    """Abstract interface for memory systems"""

    @abstractmethod
    def add(self, content: str, account_id: str, metadata: Optional[Dict] = None, trace_id: Optional[str] = None, db: Session = None, market: str = "CRYPTO"):
        """Add a memory item."""
        pass

    @abstractmethod
    def search(self, query: str, account_id: str, limit: int = 2, db: Session = None, market: str = "CRYPTO") -> List[Dict]:
        """Search for memories."""
        pass

    @abstractmethod
    def get_all(self, account_id: str, limit: int = 100, db: Session = None) -> List[Dict]:
        """Get all memories for an account."""
        pass

    @abstractmethod
    def delete(self, memory_id: str):
        """Delete a specific memory."""
        pass

    @abstractmethod
    def clear_account_memories(self, account_id: str) -> int:
        """Delete all memories for an account. Returns count deleted."""
        pass

    @abstractmethod
    def reset(self, db: Session = None):
        """Reset/clear ALL memories across all accounts."""
        pass

    @staticmethod
    def rerank(results: List[Dict], limit: int) -> List[Dict]:
        """Rerank search results by weighted combination of similarity and time decay.

        final_score = α × similarity + (1 - α) × time_decay
        where time_decay = 0.5 ^ (age_days / half_life_days)
        """
        if not results:
            return []

        half_life = getattr(AgentConfig, 'MEMORY_TIME_DECAY_HALF_LIFE_DAYS', 7)
        alpha = getattr(AgentConfig, 'MEMORY_RERANK_SIMILARITY_WEIGHT', 0.8)
        now = datetime.now(timezone.utc)

        for r in results:
            # Parse created_at
            created_at = r.get("created_at")
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    created_at = None

            if isinstance(created_at, datetime):
                created_at = MemoryInterface._normalize_created_at(created_at)

            if created_at:
                age_days = max((now - created_at).total_seconds() / 86400, 0)
                time_decay = math.pow(0.5, age_days / half_life)
            else:
                time_decay = 0.5  # Unknown age gets neutral weight

            similarity = r.get("similarity", 0)
            r["time_decay"] = round(time_decay, 4)
            r["final_score"] = round(alpha * similarity + (1 - alpha) * time_decay, 4)

        results.sort(key=lambda x: x["final_score"], reverse=True)
        return results[:limit]

    @staticmethod
    def _normalize_created_at(value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            value = value.astimezone()
        return value.astimezone(timezone.utc)


class LocalMemory(MemoryInterface):
    """
    Local memory system using sentence-transformers for embeddings.

    Features:
    - Local SQLite storage
    - Cosine similarity search
    - Fast enough for trading agent use case (5-min decision cycle)
    """

    def __init__(self):
        """Initialize the local memory system with embedding model"""
        self.model = None
        self.embedding_dim = None

        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            logger.error("sentence-transformers not installed. Memory features will be disabled.")
            return

        try:
            # Use all-MiniLM-L6-v2
            model_name = AgentConfig.MEMORY_EMBEDDING_MODEL
            logger.info(f"Loading embedding model: {model_name}")

            self.model = SentenceTransformer(model_name)
            self.embedding_dim = self.model.get_sentence_embedding_dimension()

            logger.info(f"Local memory initialized successfully. Embedding dimension: {self.embedding_dim}")
        except Exception as e:
            logger.error(f"Failed to initialize local memory: {e}")
            self.model = None

    def _compute_embedding(self, text: str) -> Optional[List[float]]:
        """Compute embedding for a text string"""
        if not self.model:
            return None
        try:
            embedding = self.model.encode(text, convert_to_numpy=True)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Failed to compute embedding: {e}")
            return None

    def add(self, content: str, account_id: str, metadata: Optional[Dict] = None, trace_id: Optional[str] = None, db: Session = None, market: str = "CRYPTO"):
        """Add a memory with its embedding to the database"""
        if not self.model:
            logger.warning("Memory model not initialized. Cannot add memory.")
            return

        _own_session = db is None
        session = db or SessionLocal()
        try:
            # Compute embedding
            embedding = self._compute_embedding(content)
            if embedding is None:
                logger.error("Failed to compute embedding for memory")
                return

            # Generate unique ID
            memory_id = str(uuid.uuid4())

            mem_entry = AgentMemory(
                memory_id=memory_id,
                account_id=int(account_id) if account_id.isdigit() else 0,
                market=market,
                trace_id=trace_id,
                content=content,
                metadata_json=metadata,
                embedding=embedding
            )
            session.add(mem_entry)
            session.commit()
            logger.info(f"Memory saved to DB for account {account_id}: {content[:100]}...")
            return memory_id

        except Exception as e:
            logger.error(f"Error adding memory: {e}")
            session.rollback()
        finally:
            if _own_session:
                session.close()

    @staticmethod
    def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        """Compute cosine similarity between two vectors"""
        try:
            v1 = np.array(vec1)
            v2 = np.array(vec2)
            return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
        except Exception as e:
            logger.error(f"Error computing cosine similarity: {e}")
            return 0.0

    def search(self, query: str, account_id: str, limit: int = 2, db: Session = None, market: str = "CRYPTO") -> List[Dict]:
        """Search for similar memories using cosine similarity"""
        if not self.model:
            logger.warning("Memory model not initialized. Cannot search.")
            return []

        _own_session = db is None
        session = db or SessionLocal()
        try:
            # Compute query embedding
            query_embedding = self._compute_embedding(query)
            if query_embedding is None:
                logger.error("Failed to compute query embedding")
                return []

            memories = session.query(AgentMemory).filter(
                AgentMemory.account_id == (int(account_id) if account_id.isdigit() else 0),
                AgentMemory.market == market
            ).all()

            if not memories:
                logger.info(f"No memories found for account {account_id}")
                return []

            # Compute similarity scores
            results = []
            for mem in memories:
                if mem.embedding is None:
                    continue

                similarity = self._cosine_similarity(query_embedding, mem.embedding)
                results.append({
                    "id": mem.memory_id,
                    "content": mem.content,
                    "metadata": mem.metadata_json or {},
                    "similarity": similarity,
                    "created_at": mem.created_at.isoformat() if mem.created_at else None
                })

            # Sort by similarity (descending) and over-fetch for rerank
            results.sort(key=lambda x: x["similarity"], reverse=True)
            top_k = getattr(AgentConfig, 'MEMORY_RERANK_TOP_K', 20)
            candidates = results[:top_k]

            # Rerank with time decay
            top_results = self.rerank(candidates, limit)

            # Update retrieval count only for high-quality matches
            if top_results:
                high_quality_ids = [r["id"] for r in top_results if r.get("similarity", 0) > AgentConfig.MEMORY_RETRIEVAL_THRESHOLD]
                if high_quality_ids:
                    try:
                        session.query(AgentMemory).filter(
                            AgentMemory.memory_id.in_(high_quality_ids)
                        ).update(
                            {
                                AgentMemory.retrieval_count: AgentMemory.retrieval_count + 1,
                                AgentMemory.last_retrieved_at: datetime.now()
                            },
                            synchronize_session=False
                        )
                        session.commit()
                    except Exception as db_error:
                        logger.error(f"Failed to update retrieval count: {db_error}")
                        session.rollback()

            logger.info(f"Found {len(top_results)} relevant memories for account {account_id}")
            return top_results

        except Exception as e:
            logger.error(f"Error searching memories: {e}")
            return []
        finally:
            if _own_session:
                session.close()

    def get_all(self, account_id: str, limit: int = 100, db: Session = None) -> List[Dict]:
        """Get all memories for an account"""
        try:
            # Use provided db session
            memories = db.query(AgentMemory).filter(
                AgentMemory.account_id == int(account_id) if account_id.isdigit() else 0
            ).order_by(AgentMemory.created_at.desc()).limit(limit).all()

            results = []
            for mem in memories:
                results.append({
                    "id": mem.memory_id,
                    "content": mem.content,
                    "metadata": mem.metadata_json or {},
                    "created_at": mem.created_at.isoformat() if mem.created_at else None
                })

            return results

        except Exception as e:
            logger.error(f"Error getting all memories: {e}")
            return []

    def delete(self, memory_id: str):
        """Delete a specific memory"""
        try:
            db: Session = SessionLocal()
            try:
                db.query(AgentMemory).filter(AgentMemory.memory_id == memory_id).delete()
                db.commit()
                logger.info(f"Memory {memory_id} deleted")
            except Exception as e:
                logger.error(f"Failed to delete memory from DB: {e}")
                db.rollback()
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error deleting memory: {e}")

    def clear_account_memories(self, account_id: str) -> int:
        """Delete all memories for an account from SQLite"""
        try:
            db: Session = SessionLocal()
            try:
                count = db.query(AgentMemory).filter(AgentMemory.account_id == int(account_id)).count()
                db.query(AgentMemory).filter(AgentMemory.account_id == int(account_id)).delete()
                db.commit()
                logger.info(f"Cleared {count} memories for account {account_id}")
                return count
            except Exception as e:
                logger.error(f"Failed to clear memories: {e}")
                db.rollback()
                return 0
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error clearing account memories: {e}")
            return 0

    def reset(self, db: Session = None):
        """Reset all memories from SQLite"""
        try:
            session = db or SessionLocal()
            try:
                session.query(AgentMemory).delete()
                session.commit()
                logger.info("All memories cleared from SQLite")
            except Exception as e:
                logger.error(f"Failed to reset memories: {e}")
                session.rollback()
            finally:
                if not db:
                    session.close()
        except Exception as e:
            logger.error(f"Error resetting memories: {e}")


def get_memory_service() -> Optional[MemoryInterface]:
    """
    Get the memory service instance based on configuration.
    Returns PineconeMemory, ChromaMemory, or LocalMemory based on config.
    """
    if not SENTENCE_TRANSFORMERS_AVAILABLE:
        logger.warning("sentence-transformers is not installed.")
        logger.warning("Install with: pip install sentence-transformers")
        return None

    # Choose backend based on configuration
    backend = getattr(AgentConfig, 'MEMORY_BACKEND', 'local')

    if backend == 'pinecone':
        try:
            from .memory_pinecone import PineconeMemory, PINECONE_AVAILABLE
            if PINECONE_AVAILABLE:
                api_key = AgentConfig.PINECONE_API_KEY
                if not api_key:
                    logger.warning("PINECONE_API_KEY not set. Falling back to LocalMemory.")
                    return LocalMemory()

                index_name = AgentConfig.PINECONE_INDEX_NAME
                environment = AgentConfig.PINECONE_ENVIRONMENT
                logger.info(f"Using Pinecone memory backend (index: {index_name})")
                return PineconeMemory(api_key=api_key, index_name=index_name, environment=environment)
            else:
                logger.warning("Pinecone backend selected but pinecone-client not installed. Falling back to LocalMemory.")
                logger.warning("Install with: pip install pinecone-client")
                return LocalMemory()
        except Exception as e:
            logger.error(f"Failed to initialize Pinecone backend: {e}. Falling back to LocalMemory.")
            return LocalMemory()
    elif backend == 'chroma':
        try:
            from .memory_chroma import ChromaMemory, CHROMA_AVAILABLE
            if CHROMA_AVAILABLE:
                persist_dir = getattr(AgentConfig, 'CHROMA_PERSIST_DIR', './chroma_db')
                logger.info(f"Using Chroma memory backend (persist_dir: {persist_dir})")
                return ChromaMemory(persist_directory=persist_dir)
            else:
                logger.warning("Chroma backend selected but chromadb not installed. Falling back to LocalMemory.")
                logger.warning("Install with: pip install chromadb")
                return LocalMemory()
        except Exception as e:
            logger.error(f"Failed to initialize Chroma backend: {e}. Falling back to LocalMemory.")
            return LocalMemory()
    else:
        logger.info("Using LocalMemory backend (SQLite)")
        return LocalMemory()
