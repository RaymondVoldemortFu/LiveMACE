from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable
from .llm_client import LLMClient
from .tools import ToolRegistry


class BaseAgent(ABC):
    def __init__(self, llm: LLMClient, tools: ToolRegistry):
        self.llm = llm
        self.tools = tools

    @abstractmethod
    def run(self, portfolio: Dict[str, Any], prices: Dict[str, float], on_step: Optional[Callable[[Dict], None]] = None) -> Dict[str, Any]:
        """Execute the agent's decision making process."""
        pass

