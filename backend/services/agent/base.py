from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Dict, Any, Optional, Callable

from benchmark.contracts import (
    AgentRunResult,
    DecisionContext,
    ExecutedTradeRef,
    Market,
    TerminationReason,
)

from .llm_client import LLMClient
from .tools import ToolRegistry


class BaseAgent(ABC):
    """Legacy Agent base class; new extensions should implement benchmark.agents.Agent."""

    def __init__(self, llm: LLMClient, tools: ToolRegistry, agent_name: Optional[str] = None):
        self.llm = llm
        self.tools = tools
        self.agent_name = agent_name

    @abstractmethod
    def run(self, portfolio: Dict[str, Any], prices: Dict[str, float], on_step: Optional[Callable[[Dict], None]] = None, trace_id: Optional[str] = None) -> Dict[str, Any]:
        """Execute the agent's decision making process."""
        pass


class LegacyAgentAdapter:
    """Adapt a synchronous legacy BaseAgent to the public v1 Agent SPI.

    This adapter deliberately invokes the old Agent in the current thread. It is
    a migration bridge for M04, not an async compatibility layer.
    """

    def __init__(
        self,
        agent: BaseAgent,
        on_step: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._agent = agent
        self._on_step = on_step

    def run(self, context: DecisionContext) -> AgentRunResult:
        portfolio = {
            "account": {
                "id": context.portfolio.account.id,
                "name": context.portfolio.account.name,
                "initial_capital": self._number(context.portfolio.account.initial_capital),
                "current_cash": self._number(context.portfolio.account.current_cash),
                "frozen_cash": self._number(context.portfolio.account.frozen_cash),
                "margin_used": self._number(context.portfolio.account.margin_used),
            },
            "positions": [
                {
                    "symbol": position.symbol,
                    "market": position.market.value,
                    "quantity": self._number(position.quantity),
                    "available_quantity": self._number(position.available_quantity),
                    "avg_cost": self._number(position.avg_cost),
                    "leverage": position.leverage,
                    "side": position.side,
                }
                for position in context.portfolio.positions
            ],
            "total_assets": self._number(context.portfolio.total_assets),
        }
        prices = {
            symbol: self._number(price)
            for symbol, price in context.portfolio.prices.items()
        }
        legacy_result = self._agent.run(
            portfolio=portfolio,
            prices=prices,
            on_step=self._on_step,
            trace_id=context.trace_id,
        )
        if not isinstance(legacy_result, dict):
            raise TypeError("legacy Agent must return a dict")

        trades = tuple(
            self._trade_ref(item)
            for item in legacy_result.get("executed_trades", ())
            if isinstance(item, dict)
        )
        operation = str(legacy_result.get("operation") or "").lower()
        termination = (
            TerminationReason.HOLD
            if operation == "hold" and not trades
            else TerminationReason.TRADE_DONE
            if trades
            else TerminationReason.MAX_STEPS
        )
        return AgentRunResult(
            trace_id=context.trace_id,
            decision_round_id=context.decision_round_id,
            termination_reason=termination,
            executed_trades=trades,
            summary=str(legacy_result.get("reason") or ""),
            metadata={"legacy_protocol": str(legacy_result.get("protocol") or "unknown")},
        )

    @staticmethod
    def _trade_ref(item: Dict[str, Any]) -> ExecutedTradeRef:
        market_value = str(item.get("market") or "CRYPTO").upper()
        market = Market.US if market_value == "US" else Market.CRYPTO
        return ExecutedTradeRef(
            operation=str(item.get("operation") or "unknown"),
            symbol=str(item.get("symbol") or "UNKNOWN"),
            market=market,
            order_id=LegacyAgentAdapter._positive_int_or_none(item.get("order_id")),
            trade_id=LegacyAgentAdapter._positive_int_or_none(item.get("trade_id")),
            executed=bool(item.get("executed", True)),
            reject_code=item.get("reject_code"),
        )

    @staticmethod
    def _positive_int_or_none(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _number(value: Decimal) -> float:
        return float(value)


__all__ = ["BaseAgent", "LegacyAgentAdapter"]
