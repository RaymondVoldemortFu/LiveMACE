"""
Local memory system using sentence-transformers.
No external services required - all embeddings and storage are local.
"""
import logging
import json
import uuid
import numpy as np
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime
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
    def add(self, content: str, account_id: str, metadata: Optional[Dict] = None, trace_id: Optional[str] = None):
        """Add a memory item."""
        pass

    @abstractmethod
    def search(self, query: str, account_id: str, limit: int = 5) -> List[Dict]:
        """Search for memories."""
        pass

    @abstractmethod
    def get_all(self, account_id: str, limit: int = 100) -> List[Dict]:
        """Get all memories for an account."""
        pass

    @abstractmethod
    def delete(self, memory_id: str):
        """Delete a specific memory."""
        pass


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

    def add(self, content: str, account_id: str, metadata: Optional[Dict] = None, trace_id: Optional[str] = None):
        """Add a memory with its embedding to the database"""
        if not self.model:
            logger.warning("Memory model not initialized. Cannot add memory.")
            return

        try:
            # Compute embedding
            embedding = self._compute_embedding(content)
            if embedding is None:
                logger.error("Failed to compute embedding for memory")
                return

            # Generate unique ID
            memory_id = str(uuid.uuid4())

            # Save to database
            db: Session = SessionLocal()
            try:
                mem_entry = AgentMemory(
                    memory_id=memory_id,
                    account_id=int(account_id) if account_id.isdigit() else 0,
                    trace_id=trace_id,
                    content=content,
                    metadata_json=metadata,
                    embedding=embedding  # Store as JSON array
                )
                db.add(mem_entry)
                db.commit()
                logger.info(f"Memory saved to DB for account {account_id}: {content[:100]}...")
            except Exception as e:
                logger.error(f"Failed to save memory to DB: {e}")
                db.rollback()
                raise
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Error adding memory: {e}")

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Compute cosine similarity between two vectors"""
        try:
            v1 = np.array(vec1)
            v2 = np.array(vec2)
            return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
        except Exception as e:
            logger.error(f"Error computing cosine similarity: {e}")
            return 0.0

    def search(self, query: str, account_id: str, limit: int = 2) -> List[Dict]:
        """Search for similar memories using cosine similarity"""
        if not self.model:
            logger.warning("Memory model not initialized. Cannot search.")
            return []

        try:
            # Compute query embedding
            query_embedding = self._compute_embedding(query)
            if query_embedding is None:
                logger.error("Failed to compute query embedding")
                return []

            # Fetch all memories for this account from database
            db: Session = SessionLocal()
            try:
                memories = db.query(AgentMemory).filter(
                    AgentMemory.account_id == int(account_id) if account_id.isdigit() else 0
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

                # Sort by similarity (descending) and return top results
                results.sort(key=lambda x: x["similarity"], reverse=True)
                top_results = results[:limit]

                logger.info(f"Found {len(top_results)} relevant memories for account {account_id}")
                return top_results

            finally:
                db.close()

        except Exception as e:
            logger.error(f"Error searching memories: {e}")
            return []

    def get_all(self, account_id: str, limit: int = 100) -> List[Dict]:
        """Get all memories for an account"""
        try:
            db: Session = SessionLocal()
            try:
                memories = db.query(AgentMemory).filter(
                    AgentMemory.account_id == int(account_id) if account_id.isdigit() else 0
                ).order_by(AgentMemory.created_at.desc()).limit(limit).all()

                results = []
                for mem in memories:
                    results.append({
                        "id": mem.memory_id,
                        "memory": mem.content,
                        "content": mem.content,
                        "metadata": mem.metadata_json or {},
                        "created_at": mem.created_at.isoformat() if mem.created_at else None
                    })

                return results

            finally:
                db.close()

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


def get_memory_service() -> Optional[MemoryInterface]:
    """
    Get the memory service instance.
    Returns LocalCPUMemory if enabled, None otherwise.
    """
    if AgentConfig.MEMORY_ENABLED:
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            logger.warning("AgentConfig.MEMORY_ENABLED is True, but sentence-transformers is not installed.")
            logger.warning("Install with: pip install sentence-transformers")
            return None
        return LocalMemory()
    return None

