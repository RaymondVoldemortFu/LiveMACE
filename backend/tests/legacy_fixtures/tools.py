from sqlalchemy.orm import Session
from services.agent.tools import Tool
from services.agent.trade_execution_tool import execute_trade_tool
from services.container_service import ContainerService
from services.security.api_key_security import resolve_runtime_api_key
from repositories.account_repo import get_account
from repositories.position_repo import list_positions
from services.agent.sub_agents.search_agent import SearchSubAgent

from benchmark.builtin.tools.account import AccountStateTool, _serialize_account, _serialize_position
from benchmark.builtin.tools.legacy import as_legacy_tool, register_legacy_tools
from benchmark.builtin.tools.market import KLINE_MODE_PRESETS, MarketToolsProvider
from benchmark.builtin.tools.memory import MemoryToolsProvider
from benchmark.builtin.tools.sandbox import SandboxToolsProvider
from benchmark.builtin.tools.search import SearchToolsProvider


def map_operation_side(operation: str, direction: str):
    """统一转换为内部订单系统使用的 side 字段."""
    operation = operation.lower()
    direction = direction.lower()

    if operation == "open":
        return "LONG" if direction == "long" else "SHORT"

    if operation == "close":
        return "SELL" if direction == "long" else "BUY"

    return None


def register_default_tools(
    registry,
    db: Session,
    account_id: int,
    trace_id: str = None,
    runtime_api_key: str = None,
):
    def account_reader(_account_id: int):
        return {
            "account": _serialize_account(get_account(db, _account_id)),
            "positions": [_serialize_position(item) for item in list_positions(db, _account_id)],
        }

    registry.register(
        as_legacy_tool(
            AccountStateTool(account_reader=account_reader),
            account_id=account_id,
            trace_id=trace_id,
            metadata={"tier": "required"},
        )
    )

    account = get_account(db, account_id)
    search_agent_api_key = runtime_api_key or resolve_runtime_api_key(account.api_key)
    search_agent = SearchSubAgent(
        model=account.model,
        api_key=search_agent_api_key,
        base_url=account.base_url,
        agent_name=account.name,
    )
    search_provider = SearchToolsProvider(
        search_runner=lambda query, topic, time_range, max_results: search_agent.run(
            query, topic, time_range, max_results
        )
    )
    sandbox = ContainerService()
    market_provider = MarketToolsProvider(sandbox=sandbox)
    sandbox_provider = SandboxToolsProvider(sandbox=sandbox)

    register_legacy_tools(
        registry,
        market_provider.list_tools(),
        account_id=account_id,
        trace_id=trace_id,
        metadata={"tier": "required"},
    )
    register_legacy_tools(
        registry,
        search_provider.list_tools(),
        account_id=account_id,
        trace_id=trace_id,
        metadata={"tier": "important"},
    )
    register_legacy_tools(
        registry,
        sandbox_provider.list_tools(),
        account_id=account_id,
        trace_id=trace_id,
        metadata={"tier": "important"},
    )

    if account and account.memory_enabled == "true":
        memory_provider = MemoryToolsProvider(
            db=db,
            trace_id=trace_id,
            bound_account_id=str(account_id),
        )
        register_legacy_tools(
            registry,
            memory_provider.list_tools(),
            account_id=account_id,
            trace_id=trace_id,
        )

    registry.register(
        Tool(
            name="execute_trade",
            description=(
                "Execute a real trade immediately. Supports multiple decision modes: "
                "1) size_mode=portion + target_portion_of_balance (ratio-based); "
                "2) size_mode=usd + usd_amount (USD-based); "
                "3) operation=all_in (full-position entry); "
                "4) operation=close_all (full liquidation)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["open", "close", "hold", "all_in", "close_all"],
                        "description": "Trading operation"
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Trading symbol. Can be omitted when using close_all for all positions."
                    },
                    "market": {
                        "type": "string",
                        "enum": ["CRYPTO", "US"],
                        "default": "CRYPTO",
                        "description": "Market type"
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["long", "short"],
                        "default": "long",
                        "description": "Direction"
                    },
                    "size_mode": {
                        "type": "string",
                        "enum": ["portion", "usd", "all_in", "close_all"],
                        "default": "portion",
                        "description": "Position sizing mode"
                    },
                    "target_portion_of_balance": {
                        "type": "number",
                        "description": "Target position ratio in portion mode [0,1]"
                    },
                    "usd_amount": {
                        "type": "number",
                        "description": "Trade amount in USD mode"
                    },
                    "close_ratio": {
                        "type": "number",
                        "description": "Close ratio [0,1], used for close + portion"
                    },
                    "leverage": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 1,
                        "description": "Leverage multiplier (US market will always use 1)"
                    },
                    "reason": {
                        "type": "string",
                        "description": "Execution reason (for logging)"
                    }
                },
                "required": ["operation"]
            },
            func=lambda **kwargs: execute_trade_tool(db=db, account_id=account_id, **kwargs)
        )
    )
