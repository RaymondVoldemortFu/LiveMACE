from typing import Optional
from .base import BaseAgent
from .react import ReActAgent
from .multi_agent import MultiAgent
from .llm_client import LLMClient
from .tools import ToolRegistry
from config.agent_config import AgentConfig

def create_agent(agent_type: str, llm: LLMClient, tools: ToolRegistry, **kwargs) -> BaseAgent:
    """
    Factory function to create an agent instance based on the agent_type.
    
    Args:
        agent_type: The type of agent to create (e.g. "react", "multi_agent")
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
        user_id = kwargs.get("user_id")
        return ReActAgent(llm, tools, max_steps=max_steps, user_id=user_id)
        
    elif normalized_type == "multi_agent":
        max_steps = kwargs.get("max_steps", 15) # Default less steps for manager loop
        user_id = kwargs.get("user_id")
        return MultiAgent(llm, tools, max_steps=max_steps, user_id=user_id)
        
    elif normalized_type == "advanced_multi_agent":
        from .multi_agent_advanced import AdvancedMultiAgent
        max_steps = kwargs.get("max_steps", 30)
        user_id = kwargs.get("user_id")
        return AdvancedMultiAgent(llm, tools, max_steps=max_steps, user_id=user_id)
    else:
        # Fallback to ReAct if unknown, but log warning
        # For now, explicit error is better for debugging
        raise ValueError(f"Unknown agent type: {agent_type}")

