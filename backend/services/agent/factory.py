from typing import Optional
from .base import BaseAgent
from .react import ReActAgent
from .llm_client import LLMClient
from .tools import ToolRegistry
from config.agent_config import AgentConfig

def create_agent(agent_type: str, llm: LLMClient, tools: ToolRegistry, **kwargs) -> BaseAgent:
    """
    Factory function to create an agent instance based on the agent_type.
    
    Args:
        agent_type: The type of agent to create (e.g. "react", "default")
        llm: The LLM client to use
        tools: The tool registry to use
        **kwargs: Additional arguments for specific agent implementations
    
    Returns:
        An instance of a class inheriting from BaseAgent
    """
    if not agent_type:
        agent_type = "react"
        
    normalized_type = agent_type.lower()
    
    if normalized_type == "react" or normalized_type == "default":
        max_steps = kwargs.get("max_steps", AgentConfig.MAX_STEPS)
        return ReActAgent(llm, tools, max_steps=max_steps)
    else:
        # Fallback or error. For now, since we only have ReAct, we could default to it,
        # but the prompt implies support for "more types" so explicit error is better for future debugging.
        # However, to avoid breaking existing configs that might have random strings (unlikely but possible),
        # strict validation is good.
        raise ValueError(f"Unknown agent type: {agent_type}")

