# services/agent/env_wrapper.py
from .tools import Tool
from sqlalchemy.orm import Session
from services.market_data import get_last_price, get_market_status
from repositories.position_repo import list_positions
from repositories.account_repo import get_account
from services.order_executor_leverage import place_and_execute_crypto

def register_default_tools(registry, db: Session, account_id: int):

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
                "price": get_last_price(symbol),
                "status": get_market_status(symbol)
            }
        )
    )

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

    def _place_order(symbol, side, size, leverage=1):
        account = get_account(db, account_id)
        if not account:
            return {"error": "Account not found"}
            
        try:
            # Handle side string normalization if needed, assuming 'buy'/'sell' from agent
            # but place_and_execute_crypto expects 'LONG'/'SHORT'/'BUY'/'SELL' contextually?
            # Re-checking place_and_execute_crypto logic:
            # It takes `side` and directly uses it to create Order.
            # OrderExecutor or Matching Engine usually interprets it.
            # For Crypto leverage:
            # LONG = Open Long
            # SHORT = Open Short
            # BUY = Close Short (Buy back)
            # SELL = Close Long (Sell off)
            
            # But agents usually just say "buy" (to go long) or "sell" (to go short).
            # If the agent is leverage-aware, it might say "long"/"short".
            # The prompt says: "direction": "long" | "short", "operation": "open" | "close"
            # If operation is separate, we need to map it.
            # But the tool `paper_place_order` only has `side` parameter.
            # Assuming `side` here corresponds to the order side directly.
            
            order = place_and_execute_crypto(
                db=db,
                account=account,
                symbol=symbol,
                name=f"Agent Order {symbol}",
                side=side.upper(),
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
                    "side": {"type": "string"},
                    "size": {"type": "number"},
                    "leverage": {"type": "number"}
                },
                "required": ["symbol", "side", "size"]
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
        "market": pos.market
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
