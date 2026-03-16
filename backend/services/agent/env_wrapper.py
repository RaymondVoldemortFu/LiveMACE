from .tools import Tool
from datetime import datetime
from sqlalchemy.orm import Session
from services.market_data import get_last_price, get_market_status, get_kline_data
from repositories.position_repo import list_positions
from repositories.account_repo import get_account
from services.order_executor_leverage import place_and_execute_crypto
from services.agent.sub_agents.search_agent import SearchSubAgent
from services.container_service import ContainerService
from config.agent_config import AgentConfig
from .memory_tools import create_memory_tools


def map_operation_side(operation: str, direction: str):
    """统一转换为内部订单系统使用的 side 字段."""
    operation = operation.lower()
    direction = direction.lower()

    if operation == "open":
        return "LONG" if direction == "long" else "SHORT"

    if operation == "close":
        return "SELL" if direction == "long" else "BUY"

    return None


def register_default_tools(registry, db: Session, account_id: int, trace_id: str = None):
    # === 行情工具 ===
    registry.register(
        Tool(
            name="get_market_snapshot",
            description="获取某个币种/股票的最新行情数据",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "market": {"type": "string", "description": "CRYPTO 或 US", "default": "CRYPTO"}
                },
                "required": ["symbol"]
            },
            func=lambda symbol, market="CRYPTO": {
                "symbol": symbol,
                "market": market,
                "price": float(get_last_price(symbol, market)),
                "market_status": get_market_status(symbol, market)
            }
        )
    )

    # === 虚拟环境工具 (Docker) ===
    container_service = ContainerService()

    registry.register(
        Tool(
            name="get_kline_history",
            description="获取指定币种/股票在指定时间范围内的K线数据并保存到虚拟环境的文件中。返回文件路径和读取建议。",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "交易对符号, e.g. BTC"
                    },
                    "market": {
                        "type": "string",
                        "description": "市场标识, CRYPTO 或 US",
                        "default": "CRYPTO"
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
            func=lambda symbol, interval, start_time, end_time=None, market="CRYPTO": _get_kline_and_save(
                container_service, account_id, symbol, interval, start_time, end_time, market
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
        model=get_account(db, account_id).model,
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
            description="在虚拟环境中运行Python脚本。会自动保存为临时文件并执行。\n脚本必须使用 print() 函数输出结果，否则将看不到任何输出。脚本不会像REPL那样自动打印最后一行表达式的值。调用时参数必须是严格 JSON：{\"script_content\": \"<python code>\"}。只能使用双引号，不能使用单引号。",
            parameters={
                "type": "object",
                "properties": {
                    "script_content": {"type": "string", "description": "Python脚本内容。务必包含 print() 语句来输出分析结果。"}
                },
                "required": ["script_content"]
            },
            func=lambda script_content: _run_python_helper(container_service, account_id, script_content)
        )
    )

    # === Memory tools (if enabled for this account) ===
    account = get_account(db, account_id)
    if account and account.memory_enabled == "true":
        memory_add_tool, memory_search_tool = create_memory_tools(db, trace_id=trace_id)
        registry.register(memory_add_tool)
        registry.register(memory_search_tool)


def _run_python_helper(service, account_id, content):
    # Save to workspace
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    filename = f"script_{timestamp}.py"
    filepath = f"/workspace/{filename}"

    # Normalize escaped newlines from tool input
    if "\\n" in content and "\n" not in content:
        content = content.replace("\\n", "\n").replace("\\t", "\t")
    
    write_res = service.write_file(account_id, filepath, content)
    
    # Simple check, though write_file returns "Success" or error msg
    if write_res != "Success":
        return {"error": f"Failed to write script: {write_res}"}
    
    # Use python3 -u for unbuffered output to ensure stdout is captured
    # Run in /workspace directory
    # We want to capture the output of the script, so we don't need to be too fancy with the shell command
    # but we do need to make sure the script runs.
    cmd = f"cd /workspace && python3 -u {filename}"
    
    exit_code, output = service.execute_command(account_id, cmd)
    
    # Fallback: if output is empty and exit code is 0, it might be that the script didn't print anything.
    # But looking at the user's log, the script ends with "analysis_result", which in a python shell would print,
    # but in a script execution (python file.py) does NOT print anything unless printed explicitly.
    # We should wrap the user content to ensure the last expression is printed if it's not a print statement?
    # Or better, just tell the user (agent) via system prompt or tool description that they must print() the result.
    # However, to be helpful, if the output is empty, we can check if the file exists and maybe cat it? No.
    
    # Let's try to capture the result by modifying how we run it? 
    # No, simplicity is better. The issue is likely that the agent wrote a script that *returns* a value
    # (like the last line `analysis_result`) but didn't `print()` it.
    # Python scripts don't output the last expression like a REPL.
    # We can auto-wrap the content? 
    # Actually, looking at the log:
    # `analysis_result = {...} \n analysis_result` -> This does NOTHING in a .py file.
    
    # Solution: We should modify the tool description to explicitly say "You must print() the final result to see it."
    # AND/OR we can try to be smart and wrap the last line in print() if it looks like an expression? 
    # Too risky. 
    
    # Alternative: Run it as `python3 -c "exec(open('filename').read()); print(locals().get('analysis_result', ''))"`? 
    # Too complex.
    
    # BEST FIX: Update tool description to remind the Agent to PRINT the output.
    # AND for the current execution, we can't easily "fix" the user's logic without parsing python.
    
    # However, the user asked to "fix all possible issues". 
    # One issue is definitely that `python3 script.py` doesn't print the last expression.
    # Let's try to append a print statement if we can detect it's missing?
    # No, that's hard.
    
    # Let's just ensure we capture EVERYTHING by redirecting stderr to stdout (already done by demux=False if working, or by 2>&1).
    # Docker's exec_run with demux=False should combine them.
    
    return {
        "exit_code": exit_code,
        "output": output if output.strip() else "(No output captured. Did you forget to print() the result?)"
    }


def _get_kline_and_save(service, account_id, symbol, interval, start_time, end_time=None, market="CRYPTO"):
    data = _get_kline_wrapper(symbol, interval, start_time, end_time, market)
    
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


def _get_kline_wrapper(symbol, interval, start_time, end_time=None, market="CRYPTO"):
    start_ts = _parse_iso_time(start_time)
    end_ts = _parse_iso_time(end_time) if end_time else None

    if not start_ts:
        return {"error": "Invalid start_time format. Please use ISO 8601 (e.g. 2023-01-01T00:00:00)."}

    # Default limit 500 if not specified by loop, but underlying service has 100 default.
    # We can pass a larger count if needed, or let it use default.
    # If end_time is far away, we might need more than 100.
    # Let's pass a larger count (e.g. 1000) to cover more ground.
    return get_kline_data(symbol, market=market, period=interval, count=1000, start_time=start_ts, end_time=end_ts)


def _parse_iso_time(time_str):
    if not time_str: return None
    try:
        # Handle Z suffix replacement for fromisoformat compatibility in older python
        ts = time_str.replace('Z', '+00:00')
        dt = datetime.fromisoformat(ts)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None
