"""Public Agent result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from .common import JsonValue, Market, _freeze_mapping, _require_non_empty


class TerminationReason(str, Enum):
    TRADE_DONE = "trade_done"
    HOLD = "hold"
    MAX_STEPS = "max_steps"
    LLM_ERROR = "llm_error"
    TOOL_ERROR = "tool_error"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ExecutedTradeRef:
    operation: str
    symbol: str
    market: Market
    order_id: int | None
    trade_id: int | None
    executed: bool
    reject_code: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.operation, "operation")
        _require_non_empty(self.symbol, "symbol")
        if not isinstance(self.market, Market):
            raise TypeError("market must be a Market")
        for name in ("order_id", "trade_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value <= 0):
                raise ValueError(f"{name} must be a positive integer or None")
        if not isinstance(self.executed, bool):
            raise TypeError("executed must be bool")


@dataclass(frozen=True)
class AgentRunResult:
    trace_id: str
    decision_round_id: str
    termination_reason: TerminationReason
    executed_trades: tuple[ExecutedTradeRef, ...] = ()
    summary: str = ""
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_non_empty(self.trace_id, "trace_id")
        _require_non_empty(self.decision_round_id, "decision_round_id")
        if not isinstance(self.termination_reason, TerminationReason):
            raise TypeError("termination_reason must be TerminationReason")
        if not isinstance(self.executed_trades, tuple) or not all(
            isinstance(item, ExecutedTradeRef) for item in self.executed_trades
        ):
            raise TypeError("executed_trades must be a tuple of ExecutedTradeRef")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "metadata"))


__all__ = ["TerminationReason", "ExecutedTradeRef", "AgentRunResult"]
