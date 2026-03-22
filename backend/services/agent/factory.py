from typing import Optional
from .base import BaseAgent
from .react import ReActAgent
from .multi_agent import MultiAgent
from .llm_client import LLMClient
from .tools import ToolRegistry
from config.agent_config import AgentConfig
import os
import logging

logger = logging.getLogger(__name__)

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
        agent_name = kwargs.get("agent_name")
        return ReActAgent(llm, tools, max_steps=max_steps, user_id=user_id, agent_name=agent_name)
        
    elif normalized_type == "multi_agent":
        max_steps = kwargs.get("max_steps", 15) # Default less steps for manager loop
        user_id = kwargs.get("user_id")
        agent_name = kwargs.get("agent_name")
        return MultiAgent(llm, tools, max_steps=max_steps, user_id=user_id, agent_name=agent_name)
    
    elif normalized_type == "rule_aware":
        # Import rule-aware components
        from .rule_aware import RuleAwareAgent, RuleEngine
        
        # Get rule documents path
        rule_docs_path = kwargs.get("rule_docs_path")
        if not rule_docs_path:
            # Default to backend/config/rules directory
            rule_docs_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "config", "rules"
            )
        
        # Initialize rule engine
        rule_engine = RuleEngine(rule_docs_path)
        logger.info(f"Loaded {rule_engine.get_rule_summary()} rules for rule-aware agent")
        
        max_steps = kwargs.get("max_steps", AgentConfig.MAX_STEPS)
        user_id = kwargs.get("user_id")
        account_id = kwargs.get("account_id")
        enable_llm_audit = kwargs.get("enable_llm_audit", False)
        agent_name = kwargs.get("agent_name")

        return RuleAwareAgent(
            llm,
            tools,
            rule_engine,
            max_steps=max_steps,
            user_id=user_id,
            account_id=account_id,
            enable_llm_audit=enable_llm_audit,
            agent_name=agent_name
        )
        
    elif normalized_type == "advanced_multi_agent":
        from .multi_agent_advanced import AdvancedMultiAgent
        max_steps = kwargs.get("max_steps", 30)
        user_id = kwargs.get("user_id")
        return AdvancedMultiAgent(llm, tools, max_steps=max_steps, user_id=user_id)
    else:
        # Fallback to ReAct if unknown, but log warning
        # For now, explicit error is better for debugging
        raise ValueError(f"Unknown agent type: {agent_type}")

