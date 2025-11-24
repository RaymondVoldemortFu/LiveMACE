# services/agent/env_wrapper.py
from .tools import Tool
from services.market_data import get_snapshot
from services.position_repo import get_positions
from services.account_repo import get_account_state
from services.order_executor_leverage import simulate_order

def register_default_tools(registry):

    registry.register(
        Tool(
            name="get_market_snapshot",
            description="获取某个币种的最新行情数据",
            parameters={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"]
            },
            func=lambda symbol: get_snapshot(symbol)
        )
    )

    registry.register(
        Tool(
            name="get_account_state",
            description="获取当前账户的资金和持仓",
            parameters={"type": "object", "properties": {}},
            func=lambda: {
                "account": get_account_state(),
                "positions": get_positions()
            }
        )
    )

    registry.register(
        Tool(
            name="paper_place_order",
            description="提交模拟订单（不会影响真实账户）",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "side": {"type": "string"},
                    "size": {"type": "number"},
                    "leverage": {"type": "number"}
                },
                "required": ["symbol", "side", "size"]
            },
            func=lambda **k: simulate_order(**k)
        )
    )

    return registry
