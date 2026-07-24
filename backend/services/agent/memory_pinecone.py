"""
Pinecone-based memory system using cloud vector database.
Provides high-performance vector search with automatic fallback to LocalMemory.
"""
import logging
import json
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from config.agent_config import AgentConfig
from database.models import AgentMemory
from services.agent.memory import MemoryInterface

logger = logging.getLogger(__name__)

# Try importing dependencies
try:
    from pinecone import Pinecone, ServerlessSpec
    PINECONE_AVAILABLE = True
except ImportError:
    PINECONE_AVAILABLE = False
    logger.warning("pinecone not installed. Install with: pip install pinecone")

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    logger.warning("sentence-transformers not installed. Install with: pip install sentence-transformers")


def _is_index_already_exists_error(error: Exception) -> bool:
    """Return True when Pinecone reports index already exists (HTTP 409)."""
    text = str(error or "")
    status = getattr(error, "status", None)
    if status == 409:
        return True
    upper_text = text.upper()
    return "ALREADY_EXISTS" in upper_text or "ALREADY EXISTS" in upper_text


class PineconeMemory(MemoryInterface):
    """
    Pinecone cloud vector database memory system.

    Features:
    - Cloud-native vector search with HNSW indexing
    - Automatic metadata filtering
    - Dual storage: Pinecone for search + SQLite for backup
    - Graceful degradation on connection errors
    """

    def __init__(self, api_key: str, index_name: str, environment: str = "us-east-1"):
        """Initialize Pinecone client and embedding model"""
        self.api_key = api_key
        self.index_name = index_name
        self.environment = environment
        self.index = None
        self.model = None
        self.embedding_dim = None

        if not PINECONE_AVAILABLE:
            raise ImportError("pinecone not installed")

        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise ImportError("sentence-transformers not installed")

        try:
            # Load embedding model first to get actual dimension
            model_name = AgentConfig.MEMORY_EMBEDDING_MODEL
            logger.info(f"Loading embedding model: {model_name}")
            self.model = SentenceTransformer(model_name)
            self.embedding_dim = self.model.get_sentence_embedding_dimension()

            # Initialize Pinecone client
            pc = Pinecone(api_key=api_key)

            # Get or create index
            if index_name not in pc.list_indexes().names():
                logger.info(f"Creating Pinecone index: {index_name}")
                try:
                    pc.create_index(
                        name=index_name,
                        dimension=self.embedding_dim,
                        metric="cosine",
                        spec=ServerlessSpec(cloud="aws", region=environment)
                    )
                except Exception as create_err:
                    if _is_index_already_exists_error(create_err):
                        logger.info(
                            "Pinecone index already exists (likely concurrent startup): %s",
                            index_name,
                        )
                    else:
                        raise

            self.index = pc.Index(index_name)

            logger.info(f"Pinecone memory initialized successfully. Index: {index_name}, Dimension: {self.embedding_dim}")

        except Exception as e:
            logger.error(f"Failed to initialize Pinecone memory: {e}")
            raise

    def _compute_embedding(self, text: str) -> Optional[List[float]]:
        """Compute embedding for text"""
        if not self.model:
            return None
        try:
            embedding = self.model.encode(text, convert_to_numpy=True)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Failed to compute embedding: {e}")
            return None

    def add(self, content: str, account_id: str, metadata: Optional[Dict] = None, trace_id: Optional[str] = None, db: Session = None, market: str = "CRYPTO"):
        """Add memory to Pinecone and SQLite backup"""
        if not self.model or not self.index:
            logger.warning("Pinecone not initialized. Cannot add memory.")
            return

        try:
            # Compute embedding
            embedding = self._compute_embedding(content)
            if embedding is None:
                logger.error("Failed to compute embedding")
                return

            # Generate memory ID
            memory_id = str(uuid.uuid4())

            # Upsert to Pinecone with metadata
            self.index.upsert(vectors=[{
                "id": memory_id,
                "values": embedding,
                "metadata": {
                    "account_id": str(account_id),
                    "market": market,
                    "content": content[:1000],  # Pinecone metadata limit
                    "trace_id": trace_id or "",
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
            }])

            # Backup to SQLite
            mem_entry = AgentMemory(
                memory_id=memory_id,
                account_id=int(account_id) if account_id.isdigit() else 0,
                market=market,
                trace_id=trace_id,
                content=content,
                metadata_json=metadata,
                embedding=embedding
            )
            db.add(mem_entry)
            db.commit()

            logger.info(f"Memory saved to Pinecone and SQLite for account {account_id}: {content[:100]}...")
            return memory_id

        except Exception as e:
            logger.error(f"Error adding memory to Pinecone: {e}")
            db.rollback()

    def search(self, query: str, account_id: str, limit: int = 2, db: Session = None, market: str = "CRYPTO") -> List[Dict]:
        """Search memories using Pinecone vector similarity"""
        if not self.model or not self.index:
            logger.warning("Pinecone not initialized. Cannot search.")
            return []

        try:
            # Compute query embedding
            query_embedding = self._compute_embedding(query)
            if query_embedding is None:
                logger.error("Failed to compute query embedding")
                return []

            # Query Pinecone: over-fetch for rerank
            top_k = getattr(AgentConfig, 'MEMORY_RERANK_TOP_K', 20)
            results = self.index.query(
                vector=query_embedding,
                top_k=top_k,
                filter={
                    "account_id": {"$eq": str(account_id)},
                    "market": {"$eq": market}
                },
                include_metadata=True
            )

            # Format results
            formatted = []

            for match in results.matches:
                similarity = match.score
                formatted.append({
                    "id": match.id,
                    "content": match.metadata.get("content", ""),
                    "metadata": {},
                    "similarity": similarity,
                    "created_at": match.metadata.get("created_at")
                })

            # Rerank with time decay
            top_results = self.rerank(formatted, limit)

            # Update retrieval stats in SQLite for high-quality matches
            high_quality_ids = [r["id"] for r in top_results if r.get("similarity", 0) >= AgentConfig.MEMORY_RETRIEVAL_THRESHOLD]
            if high_quality_ids:
                try:
                    db.query(AgentMemory).filter(
                        AgentMemory.memory_id.in_(high_quality_ids)
                    ).update(
                        {
                            AgentMemory.retrieval_count: AgentMemory.retrieval_count + 1,
                            AgentMemory.last_retrieved_at: datetime.now()
                        },
                        synchronize_session=False
                    )
                    db.commit()
                except Exception as db_error:
                    logger.error(f"Failed to update retrieval count: {db_error}")
                    db.rollback()

            logger.info(f"Found {len(top_results)} relevant memories from Pinecone for account {account_id}")
            return top_results

        except Exception as e:
            logger.error(f"Error searching Pinecone: {e}")
            return []

    def get_all(self, account_id: str, limit: int = 100, db: Session = None) -> List[Dict]:
        """Get all memories from SQLite backup"""
        try:
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
        """Delete memory from both Pinecone and SQLite"""
        try:
            # Delete from Pinecone
            if self.index:
                self.index.delete(ids=[memory_id])

            # Delete from SQLite
            from database.connection import SessionLocal
            db = SessionLocal()
            try:
                db.query(AgentMemory).filter(AgentMemory.memory_id == memory_id).delete()
                db.commit()
                logger.info(f"Memory {memory_id} deleted from Pinecone and SQLite")
            except Exception as e:
                logger.error(f"Failed to delete memory from SQLite: {e}")
                db.rollback()
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error deleting memory: {e}")

    def clear_account_memories(self, account_id: str) -> int:
        """Delete all memories for a given account from both Pinecone and SQLite"""
        try:
            # Get all memory IDs for this account from SQLite
            from database.connection import SessionLocal
            db = SessionLocal()
            try:
                memories = db.query(AgentMemory).filter(AgentMemory.account_id == int(account_id)).all()
                ids = [m.memory_id for m in memories]

                # Delete from Pinecone
                if self.index and ids:
                    self.index.delete(ids=ids)

                # Delete from SQLite
                db.query(AgentMemory).filter(AgentMemory.account_id == int(account_id)).delete()
                db.commit()
                logger.info(f"Cleared {len(ids)} memories for account {account_id}")
                return len(ids)
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
        """Reset all memories from both Pinecone and SQLite"""
        try:
            from database.connection import SessionLocal
            session = db or SessionLocal()
            try:
                memories = session.query(AgentMemory).all()
                ids = [m.memory_id for m in memories]

                if self.index and ids:
                    self.index.delete(ids=ids)

                session.query(AgentMemory).delete()
                session.commit()
                logger.info(f"Reset all memories: {len(ids)} deleted from Pinecone and SQLite")
            except Exception as e:
                logger.error(f"Failed to reset memories: {e}")
                session.rollback()
            finally:
                if not db:
                    session.close()
        except Exception as e:
            logger.error(f"Error resetting memories: {e}")
