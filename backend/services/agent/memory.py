import logging
import json
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from config.agent_config import AgentConfig
from database.models import AgentMemory
from database.connection import SessionLocal

# Try importing mem0, handle if not installed
try:
    from mem0 import Memory as Mem0SDK
    MEM0_AVAILABLE = True
except ImportError:
    MEM0_AVAILABLE = False

logger = logging.getLogger(__name__)

class MemoryInterface(ABC):
    @abstractmethod
    def add(self, content: str, user_id: str, metadata: Optional[Dict] = None, trace_id: Optional[str] = None):
        """Add a memory item."""
        pass

    @abstractmethod
    def search(self, query: str, user_id: str, limit: int = 5) -> List[Dict]:
        """Search for memories."""
        pass
    
    @abstractmethod
    def get_all(self, user_id: str, limit: int = 100) -> List[Dict]:
        """Get all memories for a user."""
        pass

    @abstractmethod
    def delete(self, memory_id: str):
        """Delete a specific memory."""
        pass


class Mem0AgentMemory(MemoryInterface):
    def __init__(self):
        self.client = None
        if not MEM0_AVAILABLE:
            logger.error("mem0ai package is not installed. Memory features will be disabled.")
            return

        self.config = {
            "vector_store": {
                "provider": AgentConfig.MEMORY_VECTOR_PROVIDER, # e.g., "qdrant", "chroma" or "aliyun" if supported
                "config": {
                    "collection_name": AgentConfig.MEMORY_COLLECTION_NAME,
                    # Add specific vector store config here if needed
                }
            },
            "llm": {
                "provider": AgentConfig.MEMORY_LLM_PROVIDER,
                "config": {
                    "model": AgentConfig.MEMORY_LLM_MODEL,
                    "api_key": AgentConfig.MEMORY_LLM_API_KEY,
                    "base_url": AgentConfig.MEMORY_LLM_BASE_URL,
                    "temperature": 0.1,
                }
            },
            "embedder": {
                # Assuming using OpenAI-compatible or specific provider for Alibaba Cloud Embeddings
                "provider": "openai", 
                "config": {
                    "model": AgentConfig.MEMORY_EMBEDDING_MODEL,
                    # If Alibaba Cloud embeddings are OpenAI compatible, set base_url/api_key here
                    # Otherwise, might need custom provider logic
                    "api_key": AgentConfig.MEMORY_LLM_API_KEY, # Using same key for now
                    "base_url": AgentConfig.MEMORY_LLM_BASE_URL,
                }
            }
        }
        
        try:
            self.client = Mem0SDK.from_config(self.config)
            logger.info("Mem0 Memory initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Mem0: {e}")
            self.client = None

    def add(self, content: str, user_id: str, metadata: Optional[Dict] = None, trace_id: Optional[str] = None):
        if not self.client:
            return

        try:
            # 1. Add to Mem0
            # Mem0 add returns list of added memories (facts)
            result = self.client.add(content, user_id=user_id, metadata=metadata or {})
            
            # 2. Save to Relational DB (AgentMemory)
            # We assume result contains the extracted facts or the added memory details
            # If result is just success/fail, we might just save the raw content.
            # Typically Mem0 returns: {'id': ..., 'event': ..., 'data': ...}
            
            db: Session = SessionLocal()
            try:
                # We save the raw input content as a "memory source" or the extracted facts if available.
                # Since Mem0 extracts facts, we iterate over result if it's a list.
                
                memories_to_save = []
                if isinstance(result, list):
                    memories_to_save = result
                elif isinstance(result, dict):
                    if "results" in result:
                        memories_to_save = result["results"]
                    else:
                        memories_to_save = [result]
                
                # If Mem0 just returns success without data, we use the input content
                if not memories_to_save:
                    # Create a generic memory entry
                    mem_entry = AgentMemory(
                        memory_id=f"raw_{datetime.now().timestamp()}", # Fallback ID
                        account_id=int(user_id) if user_id.isdigit() else 0, # Assuming user_id is account_id
                        trace_id=trace_id,
                        content=content,
                        metadata_json=metadata,
                        vector_id=None
                    )
                    db.add(mem_entry)
                else:
                    for mem in memories_to_save:
                        # mem structure depends on Mem0 version, assuming it has 'memory' or 'text' and 'id'
                        mem_content = mem.get("memory") or mem.get("text") or content
                        mem_id = mem.get("id")
                        
                        mem_entry = AgentMemory(
                            memory_id=str(mem_id),
                            account_id=int(user_id) if user_id.isdigit() else 0,
                            trace_id=trace_id,
                            content=mem_content,
                            metadata_json=metadata,
                            vector_id=str(mem_id)
                        )
                        db.add(mem_entry)
                
                db.commit()
                logger.info(f"Memory saved to DB and Mem0 for user {user_id}")
                
            except Exception as e:
                logger.error(f"Failed to save memory to DB: {e}")
                db.rollback()
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Error adding memory: {e}")

    def search(self, query: str, user_id: str, limit: int = 5) -> List[Dict]:
        if not self.client:
            return []
        try:
            results = self.client.search(query, user_id=user_id, limit=limit)
            return results
        except Exception as e:
            logger.error(f"Error searching memory: {e}")
            return []

    def get_all(self, user_id: str, limit: int = 100) -> List[Dict]:
        if not self.client:
            return []
        try:
            return self.client.get_all(user_id=user_id, limit=limit)
        except Exception as e:
            logger.error(f"Error getting all memories: {e}")
            return []

    def delete(self, memory_id: str):
        if not self.client:
            return
        try:
            self.client.delete(memory_id)
            
            # Also delete from DB
            db: Session = SessionLocal()
            try:
                db.query(AgentMemory).filter(AgentMemory.memory_id == memory_id).delete()
                db.commit()
            except Exception as e:
                logger.error(f"Failed to delete memory from DB: {e}")
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Error deleting memory: {e}")


def get_memory_service() -> MemoryInterface:
    if AgentConfig.MEMORY_ENABLED:
        if not MEM0_AVAILABLE:
            logger.warning("AgentConfig.MEMORY_ENABLED is True, but mem0ai is not installed. Memory will be disabled.")
            return None
        return Mem0AgentMemory()
    return None

