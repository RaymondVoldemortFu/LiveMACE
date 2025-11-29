from .tools import Tool
from sqlalchemy.orm import Session
from services.market_data import get_last_price, get_market_status
from repositories.position_repo import list_positions
from repositories.account_repo import get_account
from services.order_executor_leverage import place_and_execute_crypto


def map_operation_side(operation: str, direction: str):
    """统一转换为内部订单系统使用的 side 字段."""
    operation = operation.lower()
    direction = direction.lower()

    if operation == "open":
        return "LONG" if direction == "long" else "SHORT"

    if operation == "close":
        return "SELL" if direction == "long" else "BUY"

    return None


def register_default_tools(registry, db: Session, account_id: int):
    # === 行情工具 ===
    registry.register(
        Tool(
            name="get_market_snapshot",
            description="获取某个币种的最新行情数据",
            parameters={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"]
            },
            func=lambda symbol: {
                "symbol": symbol,
                "price": float(get_last_price(symbol)),
                "market_status": get_market_status(symbol)
            }
        )
    )

    # === 账户工具 ===
    registry.register(
        Tool(
            name="get_account_state",
            description="获取当前账户的资金和持仓",
            parameters={"type": "object", "properties": {}},
            func=lambda: {
                "account": _serialize_account(get_account(db, account_id)),
                "positions": [_serialize_position(p) for p in list_positions(db, account_id)]
            }
        )
    )

    # === 下单工具（包含 operation + direction） ===
    def _place_order(symbol, operation, direction, size, leverage=1):
        account = get_account(db, account_id)
        if not account:
            return {"error": "Account not found"}

        side = map_operation_side(operation, direction)
        if side is None:
            return {"error": f"Invalid operation={operation}, direction={direction}"}

        try:
            order = place_and_execute_crypto(
                db=db,
                account=account,
                symbol=symbol,
                name=f"Agent Order {symbol}",
                side=side,
                order_type="MARKET",
                price=None,
                quantity=size,
                leverage=leverage
            )
            return _serialize_order(order)
        except Exception as e:
            return {"error": str(e)}

    registry.register(
        Tool(
            name="paper_place_order",
            description="提交模拟订单（不会影响真实账户）",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "operation": {"type": "string"},
                    "direction": {"type": "string"},
                    "size": {"type": "number"},
                    "leverage": {"type": "number"}
                },
                "required": ["symbol", "operation", "direction", "size"]
            },
            func=_place_order
        )
    )

    return registry


def _serialize_account(account):
    if not account: return None
    return {
        "id": account.id,
        "name": account.name,
        "cash": float(account.current_cash),
        "frozen": float(account.frozen_cash),
    }


def _serialize_position(pos):
    return {
        "symbol": pos.symbol,
        "quantity": float(pos.quantity),
        "avg_cost": float(pos.avg_cost),
        "leverage": pos.leverage,
        "side": pos.side,
        "market": str(pos.market)
    }


def _serialize_order(order):
    return {
        "order_no": order.order_no,
        "symbol": order.symbol,
        "side": order.side,
        "price": float(order.price) if order.price else 0.0,
        "quantity": float(order.quantity),
        "status": order.status,
        "filled_quantity": float(order.filled_quantity)
    }
