from abc import ABC, abstractmethod
from typing import Dict, Any, List
from datetime import datetime

class BaseEvaluator(ABC):
    """
    Base class for agent evaluators.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the evaluation metric"""
        pass

    @abstractmethod
    def evaluate(self, agent_data: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate the agent's performance.
        
        Args:
            agent_data: Data related to the agent's actions (trades, decisions, traces)
            market_data: Data related to the market during the evaluation period
            
        Returns:
            A dictionary containing evaluation metrics.
        """
        pass

