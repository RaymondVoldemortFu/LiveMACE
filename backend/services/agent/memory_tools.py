"""
Memory tools for Agent to actively manage its memory.
Agent can call these tools to add experiences or search for relevant memories.
"""
import logging
from typing import Optional
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


def memory_add(experience: str, user_id: str, metadata: Optional[str] = None) -> str:
    """
    Add an experience or insight to memory.

    Args:
        experience: The experience, insight, or lesson learned that should be remembered.
                   Should be a concise summary of what was learned.
        user_id: The user/account ID this memory belongs to.
        metadata: Optional JSON string with additional metadata (e.g., {"trade_result": "profit"})

    Returns:
        Success message or error message.

    Example:
        memory_add(
            experience="When BTC drops 5% in 1 hour with high volume, it often rebounds within 2 hours. Consider buying the dip.",
            user_id="123"
        )
    """
    try:
        memory_service = get_or_create_memory_service()

        if not memory_service:
            return "Memory service is not available. Memory not saved."

        # Parse metadata if provided
        import json
        metadata_dict = {}
        if metadata:
            try:
                metadata_dict = json.loads(metadata)
            except json.JSONDecodeError:
                logger.warning(f"Invalid metadata JSON: {metadata}")

        # Add to memory
        memory_service.add(
            content=experience,
            user_id=user_id,
            metadata=metadata_dict
        )

        logger.info(f"Memory added for user {user_id}: {experience[:100]}...")
        return f"✓ Memory saved successfully: '{experience[:100]}...'"

    except Exception as e:
        error_msg = f"Failed to add memory: {str(e)}"
        logger.error(error_msg)
        return f"✗ {error_msg}"


def memory_search(query: str, user_id: str, limit: int = 5) -> str:
    """
    Search for relevant memories based on a query.

    Args:
        query: The search query describing what kind of memories you need.
               Should be specific about the context or situation.
        user_id: The user/account ID to search memories for.
        limit: Maximum number of memories to return (default: 5, max: 10)

    Returns:
        Formatted string with relevant memories, or message if no memories found.

    Example:
        memory_search(
            query="What did I learn about BTC price drops and rebounds?",
            user_id="123"
        )
    """
    try:
        memory_service = get_or_create_memory_service()

        if not memory_service:
            return "Memory service is not available."

        # Limit to max 10
        limit = min(limit, 10)

        # Search memories
        results = memory_service.search(
            query=query,
            user_id=user_id,
            limit=limit
        )

        if not results:
            return f"No relevant memories found for query: '{query}'"

        # Format results
        formatted_memories = []
        for idx, memory in enumerate(results, 1):
            # Extract memory text (Mem0 might return different formats)
            text = memory.get('memory') or memory.get('text') or memory.get('content') or str(memory)

            # Extract metadata if available
            metadata = memory.get('metadata', {})
            metadata_str = f" [metadata: {metadata}]" if metadata else ""

            formatted_memories.append(f"{idx}. {text}{metadata_str}")

        result_text = "\n".join(formatted_memories)
        logger.info(f"Found {len(results)} memories for user {user_id}")

        return f"Found {len(results)} relevant memories:\n{result_text}"

    except Exception as e:
        error_msg = f"Failed to search memories: {str(e)}"
        logger.error(error_msg)
        return f"✗ {error_msg}"


# Tool definitions for registration
memory_add_tool = Tool(
    name="memory_add",
    description=(
        "Add an experience, insight, or lesson learned to your long-term memory. "
        "Use this when you discover a useful pattern, learn from a trade result, "
        "or want to remember important information for future decisions. "
        "The experience should be a concise, actionable insight."
    ),
    parameters={
        "type": "object",
        "properties": {
            "experience": {
                "type": "string",
                "description": (
                    "The experience or insight to remember. Should be specific and actionable. "
                    "Example: 'When BTC drops 5% with high volume, it often rebounds within 2 hours.'"
                )
            },
            "user_id": {
                "type": "string",
                "description": "Your user/account ID"
            },
            "metadata": {
                "type": "string",
                "description": "Optional JSON string with additional context (e.g., trade result, market conditions)",
                "default": None
            }
        },
        "required": ["experience", "user_id"]
    },
    func=memory_add
)

memory_search_tool = Tool(
    name="memory_search",
    description=(
        "Search your long-term memory for relevant experiences and insights. "
        "Use this when you need to recall past learnings, similar situations, "
        "or patterns that might help with the current decision. "
        "Be specific in your query about what you're looking for."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What you want to search for in your memories. Be specific. "
                    "Example: 'What did I learn about trading BTC during high volatility?'"
                )
            },
            "user_id": {
                "type": "string",
                "description": "Your user/account ID"
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of memories to return (default: 5, max: 10)",
                "default": 5
            }
        },
        "required": ["query", "user_id"]
    },
    func=memory_search
)
