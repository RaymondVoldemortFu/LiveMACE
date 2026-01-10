# services/agent/core.py
from .base import BaseAgent
from .react import ReActAgent
from .factory import create_agent
from config.agent_config import AgentConfig

# Backward compatibility alias
TradingAgent = ReActAgent
