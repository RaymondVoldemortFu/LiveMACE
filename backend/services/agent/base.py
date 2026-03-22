from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable
from .llm_client import LLMClient
from .tools import ToolRegistry


class BaseAgent(ABC):
    def __init__(self, llm: LLMClient, tools: ToolRegistry, agent_name: Optional[str] = None):
        self.llm = llm
        self.tools = tools
        self.agent_name = agent_name

    @abstractmethod
    def run(self, portfolio: Dict[str, Any], prices: Dict[str, float], on_step: Optional[Callable[[Dict], None]] = None, trace_id: Optional[str] = None) -> Dict[str, Any]:
        """Execute the agent's decision making process."""
        pass

