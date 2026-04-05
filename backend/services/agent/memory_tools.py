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

# Similarity threshold for deduplication
DEDUP_SIMILARITY_THRESHOLD = 0.90

def get_or_create_memory_service():
    """Get or create memory service singleton"""
    global _memory_service
    if _memory_service is None:
        _memory_service = get_memory_service()
    return _memory_service

def create_memory_tools(db: Session, trace_id: str = None, bound_account_id: str = None):
    """
    Factory function to create memory tools with a specific database session.

    Args:
        db: SQLAlchemy Session to use for database operations
        trace_id: Trace ID for linking memories to the agent session that created them
        bound_account_id: If set, overrides any account_id the agent passes — prevents
                          cross-account memory writes for accounts with memory disabled

    Returns:
        Tuple of (memory_add_tool, memory_search_tool)
    """
    memory_service = get_or_create_memory_service()

    def _resolve_account_id(account_id: str) -> str:
        """Resolve account_id: if it's a name instead of numeric ID, look up the real ID."""
        if account_id.isdigit():
            return account_id
        # LLM passed account name instead of ID, look it up
        from database.models import Account
        account = db.query(Account).filter(Account.name == account_id).first()
        if account:
            logger.info(f"Resolved account name '{account_id}' to ID {account.id}")
            return str(account.id)
        logger.warning(f"Could not resolve account_id '{account_id}', using as-is")
        return account_id

    def memory_add_with_db(experience: str, account_id: str, market: str = "CRYPTO", metadata: Optional[str] = None) -> dict:
        """Add memory using provided db session, with automatic deduplication"""
        try:
            if not memory_service:
                return {"status": "error", "message": "Memory service is not available"}

            # Enforce bound account — ignore whatever account_id the agent passed
            if bound_account_id:
                account_id = bound_account_id
            else:
                account_id = _resolve_account_id(account_id)

            # Hard dedup: search for similar memories before adding
            existing = memory_service.search(query=experience, account_id=account_id, limit=1, db=db, market=market)
            if existing and existing[0].get("similarity", 0) >= DEDUP_SIMILARITY_THRESHOLD:
                dup = existing[0]
                logger.info(f"Memory dedup: rejected for account {account_id} (similarity={dup['similarity']:.3f}): {experience[:80]}...")
                return {
                    "status": "rejected",
                    "message": f"Too similar to existing memory (similarity={dup['similarity']:.2f}). Not saved.",
                    "existing_memory": dup["content"]
                }

            import json
            metadata_dict = {}
            if metadata:
                try:
                    metadata_dict = json.loads(metadata)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid metadata JSON: {metadata}")

            memory_service.add(content=experience, account_id=account_id, metadata=metadata_dict, trace_id=trace_id, db=db, market=market)
            logger.info(f"Memory added for account {account_id} market {market}: {experience[:100]}...")

            return {
                "status": "success",
                "message": "Memory saved successfully",
                "memory": {"content": experience, "account_id": account_id, "metadata": metadata_dict}
            }
        except Exception as e:
            error_msg = f"Failed to add memory: {str(e)}"
            logger.error(error_msg)
            return {"status": "error", "message": error_msg}
    
    def memory_search_with_db(query: str, account_id: str, market: str = "CRYPTO", limit: int = 2) -> dict:
        """Search memories using provided db session"""
        try:
            if not memory_service:
                return {"status": "error", "message": "Memory service is not available"}

            if bound_account_id:
                account_id = bound_account_id
            else:
                account_id = _resolve_account_id(account_id)

            limit = min(limit, 5)
            results = memory_service.search(query=query, account_id=account_id, limit=limit, db=db, market=market)
            
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
                    "similarity": round(memory.get("similarity", 0), 4),
                    "final_score": memory.get("final_score"),
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
        description="Store a reusable trading rule to long-term memory. Format: [CONDITION] → [OBSERVATION] → [RULE], 1-3 sentences max. Do NOT store news, specific dates/prices, or event logs — only generalizable patterns. You MUST specify the market parameter.",
        parameters={
            "type": "object",
            "properties": {
                "experience": {"type": "string", "description": "A reusable trading rule in the format: [CONDITION] → [OBSERVATION] → [RULE]. Strip specific dates and prices. Max 1-3 sentences."},
                "account_id": {"type": "string", "description": "Your account ID"},
                "market": {"type": "string", "enum": ["CRYPTO", "US"], "description": "The market this memory belongs to. Use CRYPTO for crypto trading rules, US for US stock trading rules."},
                "metadata": {"type": "string", "description": "Optional JSON string with additional context", "default": None}
            },
            "required": ["experience", "account_id", "market"]
        },
        func=memory_add_with_db
    )

    memory_search_tool = Tool(
        name="memory_search",
        description="Search long-term memory for trading rules relevant to current market conditions. Query with pattern descriptions (e.g. 'altcoin oversold during BTC downtrend'), not specific events or dates. You MUST specify the market parameter to search only relevant memories.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A pattern description of the market condition you want rules for (e.g. 'high leverage risk in downtrend', 'SOL support breakdown patterns')"},
                "account_id": {"type": "string", "description": "Your account ID"},
                "market": {"type": "string", "enum": ["CRYPTO", "US"], "description": "The market to search memories for. Use CRYPTO for crypto rules, US for US stock rules."},
                "limit": {"type": "integer", "description": "Maximum number of memories to return (default: 2, max: 5)", "default": 2}
            },
            "required": ["query", "account_id", "market"]
        },
        func=memory_search_with_db
    )
    
    return memory_add_tool, memory_search_tool
