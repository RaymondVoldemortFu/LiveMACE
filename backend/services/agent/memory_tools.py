"""
Memory tools for Agent to actively manage its memory.
Agent can call these tools to add experiences or search for relevant memories.
"""
import logging
from typing import Optional
from sqlalchemy.orm import Session
from .memory import get_memory_service
from .tools import Tool

logger = logging.getLogger(__name__)

# Global memory service instance
_memory_service = None

def get_or_create_memory_service():
    """Get or create memory service singleton"""
    global _memory_service
    if _memory_service is None:
        _memory_service = get_memory_service()
    return _memory_service

def create_memory_tools(db: Session):
    """
    Factory function to create memory tools with a specific database session.
    
    Args:
        db: SQLAlchemy Session to use for database operations
        
    Returns:
        Tuple of (memory_add_tool, memory_search_tool)
    """
    memory_service = get_or_create_memory_service()
    
    def memory_add_with_db(experience: str, account_id: str, metadata: Optional[str] = None) -> dict:
        """Add memory using provided db session"""
        try:
            if not memory_service:
                return {"status": "error", "message": "Memory service is not available"}
            
            import json
            metadata_dict = {}
            if metadata:
                try:
                    metadata_dict = json.loads(metadata)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid metadata JSON: {metadata}")
            
            memory_service.add(content=experience, account_id=account_id, metadata=metadata_dict, db=db)
            logger.info(f"Memory added for account {account_id}: {experience[:100]}...")
            
            return {
                "status": "success",
                "message": "Memory saved successfully",
                "memory": {"content": experience, "account_id": account_id, "metadata": metadata_dict}
            }
        except Exception as e:
            error_msg = f"Failed to add memory: {str(e)}"
            logger.error(error_msg)
            return {"status": "error", "message": error_msg}
    
    def memory_search_with_db(query: str, account_id: str, limit: int = 2) -> dict:
        """Search memories using provided db session"""
        try:
            if not memory_service:
                return {"status": "error", "message": "Memory service is not available"}
            
            limit = min(limit, 5)
            results = memory_service.search(query=query, account_id=account_id, limit=limit, db=db)
            
            if not results:
                return {"status": "success", "message": "No relevant memories found", "query": query, "count": 0, "memories": []}

            # Format results
            formatted_memories = []
            for idx, memory in enumerate(results, 1):
                content = memory.get('content') or str(memory)
                metadata = memory.get('metadata', {})
                formatted_memories.append({
                    "id": memory.get("id", idx),
                    "content": content,
                    "metadata": metadata
                })

            logger.info(f"Found {len(results)} memories for account {account_id}")

            return {
                "status": "success",
                "message": f"Found {len(results)} relevant memories",
                "query": query,
                "count": len(results),
                "memories": formatted_memories
            }

        except Exception as e:
            error_msg = f"Failed to search memories: {str(e)}"
            logger.error(error_msg)
            return {"status": "error", "message": error_msg}
    
    memory_add_tool = Tool(
        name="memory_add",
        description="Store a reusable trading rule to long-term memory. Format: [CONDITION] → [OBSERVATION] → [RULE], 1-3 sentences max. Do NOT store news, specific dates/prices, or event logs — only generalizable patterns.",
        parameters={
            "type": "object",
            "properties": {
                "experience": {"type": "string", "description": "A reusable trading rule in the format: [CONDITION] → [OBSERVATION] → [RULE]. Strip specific dates and prices. Max 1-3 sentences."},
                "account_id": {"type": "string", "description": "Your account ID"},
                "metadata": {"type": "string", "description": "Optional JSON string with additional context", "default": None}
            },
            "required": ["experience", "account_id"]
        },
        func=memory_add_with_db
    )

    memory_search_tool = Tool(
        name="memory_search",
        description="Search long-term memory for trading rules relevant to current market conditions. Query with pattern descriptions (e.g. 'altcoin oversold during BTC downtrend'), not specific events or dates.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A pattern description of the market condition you want rules for (e.g. 'high leverage risk in downtrend', 'SOL support breakdown patterns')"},
                "account_id": {"type": "string", "description": "Your account ID"},
                "limit": {"type": "integer", "description": "Maximum number of memories to return (default: 2, max: 5)", "default": 2}
            },
            "required": ["query", "account_id"]
        },
        func=memory_search_with_db
    )
    
    return memory_add_tool, memory_search_tool
