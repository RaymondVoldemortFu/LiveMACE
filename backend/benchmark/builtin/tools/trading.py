"""Account-scoped synchronous trading Tool backed by the application gateway."""

from benchmark.contracts import SideEffect, TRADING_WRITE
from benchmark.builtin.tools._support import BoundCallableTool, spec

TRADING_PARAMETERS = {
    "additionalProperties": False,
    "properties": {
        "close_ratio": {
            "description": "Close ratio [0,1], used for close + portion",
            "type": "number",
        },
        "direction": {
            "default": "long",
            "description": "Direction",
            "enum": ["long", "short"],
            "type": "string",
        },
        "leverage": {
            "default": 1,
            "description": "Leverage multiplier (US market will always use 1)",
            "maximum": 10,
            "minimum": 1,
            "type": "integer",
        },
        "market": {
            "default": "CRYPTO",
            "description": "Market type",
            "enum": ["CRYPTO", "US"],
            "type": "string",
        },
        "operation": {
            "description": "Trading operation",
            "enum": ["open", "close", "hold", "all_in", "close_all"],
            "type": "string",
        },
        "reason": {"description": "Execution reason (for logging)", "type": "string"},
        "size_mode": {
            "default": "portion",
            "description": "Position sizing mode",
            "enum": ["portion", "usd", "all_in", "close_all"],
            "type": "string",
        },
        "symbol": {
            "description": "Trading symbol. Can be omitted when using close_all for "
            "all positions.",
            "type": "string",
        },
        "target_portion_of_balance": {
            "description": "Target position ratio in portion mode [0,1]",
            "type": "number",
        },
        "usd_amount": {"description": "Trade amount in USD mode", "type": "number"},
    },
    "required": ["operation"],
    "type": "object",
}
TRADING_SPEC = spec(
    "core.execute_trade",
    "Execute a real trade immediately. Supports multiple decision modes: 1) size_mode=portion + target_portion_of_balance (ratio-based); 2) size_mode=usd + usd_amount (USD-based); 3) operation=all_in (full-position entry); 4) operation=close_all (full liquidation).",
    TRADING_PARAMETERS,
    side_effect=SideEffect.TRADING_WRITE,
    capabilities=(TRADING_WRITE,),
)


class TradingToolsProvider:
    def __init__(self, *, execute=None):
        self._execute = execute

    def _invoke(self, context, arguments):
        from services.agent.trade_execution_tool import execute_trade_tool

        execute = self._execute or execute_trade_tool
        return execute(
            db=None,
            account_id=context.account_id,
            decision_round_id=context.decision_round_id,
            tool_call_id=context.call_id,
            **dict(arguments),
        )

    def list_tools(self):
        return (BoundCallableTool(TRADING_SPEC, self._invoke),)
