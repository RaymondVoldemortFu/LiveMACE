from .tools import Tool
from datetime import datetime
from sqlalchemy.orm import Session
from services.market_data import get_last_price, get_market_status, get_kline_data
from repositories.position_repo import list_positions
from repositories.account_repo import get_account
from services.order_executor_leverage import place_and_execute_crypto
from services.agent.sub_agents.search_agent import SearchSubAgent
from services.container_service import ContainerService


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
                "properties": {
                    "symbol": {"type": "string"}
                },
                "required": ["symbol"]
            },
            func=lambda symbol: {
                "symbol": symbol,
                "price": float(get_last_price(symbol)),
                "market_status": get_market_status(symbol)
            }
        )
    )

    # === 虚拟环境工具 (Docker) ===
    container_service = ContainerService()

    registry.register(
        Tool(
            name="get_kline_history",
            description="获取指定加密货币在指定时间范围内的K线数据并保存到虚拟环境的文件中。返回文件路径和读取建议。",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "交易对符号, e.g. BTC"
                    },
                    "interval": {
                        "type": "string",
                        "enum": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
                        "description": "时间分辨率"
                    },
                    "start_time": {
                        "type": "string",
                        "description": "开始时间 (ISO 8601格式, e.g. 2023-01-01T00:00:00)"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "结束时间 (ISO 8601格式), 可选"
                    }
                },
                "required": ["symbol", "interval", "start_time"]
            },
            func=lambda symbol, interval, start_time, end_time=None: _get_kline_and_save(
                container_service, account_id, symbol, interval, start_time, end_time
            )
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

    # === 搜索工具 (Sub-Agent) ===
    search_agent = SearchSubAgent(
        api_key=get_account(db, account_id).api_key,
        base_url=get_account(db, account_id).base_url
    )
    registry.register(
        Tool(
            name="consult_search_agent",
            description="网络搜索工具。当需要获取最新的市场新闻、宏观经济数据、项目动态或特定币种的非价格信息时使用。返回包含搜索结果摘要和来源的结构化数据。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "具体的搜索查询语句。"
                    },
                    "topic": {
                        "type": "string",
                        "enum": ["general", "news", "finance"],
                        "description": "搜索主题类别。"
                    },
                    "time_range": {
                        "type": "string",
                        "enum": ["day", "week", "month", "year", "none"],
                        "description": "搜索时间范围。"
                    },
                    "search_depth": {
                        "type": "string",
                        "enum": ["basic", "advanced"],
                        "description": "搜索深度。basic较快但结果较少，advanced较慢但结果更详细。",
                        "default": "basic"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返回结果的最大数量。",
                        "default": 5
                    }
                },
                "required": ["query", "topic", "time_range"]
            },
            func=lambda query, topic, time_range, search_depth="basic", max_results=5: search_agent.run(query, topic, time_range, search_depth, max_results)
        )
    )

    registry.register(
        Tool(
            name="execute_shell_command",
            description="在虚拟Linux环境中执行Shell命令。返回(exit_code, output)。",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的Shell命令"}
                },
                "required": ["command"]
            },
            func=lambda command: container_service.execute_command(account_id, command)
        )
    )

    registry.register(
        Tool(
            name="read_file",
            description="读取虚拟环境中的文件内容。内容长度受限。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件绝对路径"}
                },
                "required": ["file_path"]
            },
            func=lambda file_path: container_service.read_file(account_id, file_path)
        )
    )

    registry.register(
        Tool(
            name="write_file",
            description="向虚拟环境中的文件写入内容。如果文件不存在会自动创建，如果目录不存在也会创建。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件绝对路径"},
                    "content": {"type": "string", "description": "写入的内容"}
                },
                "required": ["file_path", "content"]
            },
            func=lambda file_path, content: container_service.write_file(account_id, file_path, content)
        )
    )

    registry.register(
        Tool(
            name="run_python_script",
            description="在虚拟环境中运行Python脚本。会自动保存为临时文件并执行。",
            parameters={
                "type": "object",
                "properties": {
                    "script_content": {"type": "string", "description": "Python脚本内容"}
                },
                "required": ["script_content"]
            },
            func=lambda script_content: _run_python_helper(container_service, account_id, script_content)
        )
    )


def _run_python_helper(service, account_id, content):
    # Save to a temporary file
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    filename = f"/tmp/script_{timestamp}.py"
    write_res = service.write_file(account_id, filename, content)
    
    # Simple check, though write_file returns "Success" or error msg
    if write_res != "Success":
        return {"error": f"Failed to write script: {write_res}"}
    
    exit_code, output = service.execute_command(account_id, f"python3 {filename}")
    return {
        "exit_code": exit_code,
        "output": output
    }


def _get_kline_and_save(service, account_id, symbol, interval, start_time, end_time=None):
    data = _get_kline_wrapper(symbol, interval, start_time, end_time)
    
    if isinstance(data, dict) and "error" in data:
        return data
    
    import json
    content = json.dumps(data, ensure_ascii=False, indent=2)
    
    # Generate filename
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    filename = f"/workspace/kline_{symbol}_{interval}_{timestamp}.json"
    
    # Save to container
    write_res = service.write_file(account_id, filename, content)
    
    if write_res != "Success":
        return {"error": f"Failed to save K-line data to container: {write_res}"}
        
    return {
        "status": "success",
        "file_path": filename,
        "message": f"K-line data saved to {filename}. You can use 'read_file' to view it (truncated) or 'run_python_script' to analyze it.",
        "data_preview": str(data)[:200] + "..." # Show a small preview
    }


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


def _get_kline_wrapper(symbol, interval, start_time, end_time=None):
    start_ts = _parse_iso_time(start_time)
    end_ts = _parse_iso_time(end_time) if end_time else None

    if not start_ts:
        return {"error": "Invalid start_time format. Please use ISO 8601 (e.g. 2023-01-01T00:00:00)."}

    # Default limit 500 if not specified by loop, but underlying service has 100 default.
    # We can pass a larger count if needed, or let it use default.
    # If end_time is far away, we might need more than 100.
    # Let's pass a larger count (e.g. 1000) to cover more ground.
    return get_kline_data(symbol, period=interval, count=1000, start_time=start_ts, end_time=end_ts)


def _parse_iso_time(time_str):
    if not time_str: return None
    try:
        # Handle Z suffix replacement for fromisoformat compatibility in older python
        ts = time_str.replace('Z', '+00:00')
        dt = datetime.fromisoformat(ts)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None
