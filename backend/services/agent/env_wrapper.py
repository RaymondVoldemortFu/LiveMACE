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
from services.agent.trade_execution_tool import execute_trade_tool


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
            description="Get the latest market snapshot for a crypto symbol or US stock.",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "market": {"type": "string", "description": "Market type: CRYPTO or US"}
                },
                "required": ["symbol", "market"]
            },
            func=lambda symbol, market: {
                "symbol": symbol,
                "market": market,
                "price": float(get_last_price(symbol, market)),
                "market_status": get_market_status(symbol, market)
            },
            metadata={"tier": "required"}
        )
    )

    # === 虚拟环境工具 (Docker) ===
    container_service = ContainerService()

    registry.register(
        Tool(
            name="get_kline_history",
            description="Fetch kline (candlestick) history for a symbol within a time range and save it to a file in the sandbox. Returns file path and reading suggestions.",
            parameters={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Trading symbol, e.g. BTC or AAPL"
                    },
                    "market": {
                        "type": "string",
                        "description": "Market identifier: CRYPTO or US"
                    },
                    "interval": {
                        "type": "string",
                        "enum": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
                        "description": "Time interval"
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Start time (ISO 8601), e.g. 2023-01-01T00:00:00"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "End time (ISO 8601), optional"
                    }
                },
                "required": ["symbol", "market", "interval", "start_time"]
            },
            func=lambda symbol, market, interval, start_time, end_time=None: _get_kline_and_save(
                container_service, account_id, symbol, interval, start_time, end_time, market
            ),
            metadata={"tier": "required"}
        )
    )

    # === 账户工具 ===
    registry.register(
        Tool(
            name="get_account_state",
            description="Get current account balances and open positions.",
            parameters={"type": "object", "properties": {}},
            func=lambda: {
                "account": _serialize_account(get_account(db, account_id)),
                "positions": [_serialize_position(p) for p in list_positions(db, account_id)]
            },
            metadata={"tier": "required"}
        )
    )

    # === 搜索工具 (Sub-Agent) ===
    account = get_account(db, account_id)
    search_agent = SearchSubAgent(
        model=account.model,
        api_key=account.api_key,
        base_url=account.base_url,
        agent_name=account.name
    )
    registry.register(
        Tool(
            name="consult_search_agent",
            description="Web search tool. Use it to retrieve latest market news, macroeconomic data, project updates, or non-price information for specific symbols. Returns structured summaries with sources.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Specific search query."
                    },
                    "topic": {
                        "type": "string",
                        "enum": ["general", "news", "finance"],
                        "description": "Search topic category."
                    },
                    "time_range": {
                        "type": "string",
                        "enum": ["day", "week", "month", "year", "none"],
                        "description": "Search time range."
                    },
                    "search_depth": {
                        "type": "string",
                        "enum": ["basic", "advanced"],
                        "description": "Search depth. basic is faster with fewer results; advanced is slower with more detailed results.",
                        "default": "basic"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return.",
                        "default": 5
                    }
                },
                "required": ["query", "topic", "time_range"]
            },
            func=lambda query, topic, time_range, search_depth="basic", max_results=5: search_agent.run(query, topic, time_range, search_depth, max_results),
            metadata={"tier": "important"}
        )
    )

    registry.register(
        Tool(
            name="execute_shell_command",
            description="Execute a shell command inside the sandboxed Linux environment. Returns (exit_code, output).",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"}
                },
                "required": ["command"]
            },
            func=lambda command: container_service.execute_command(account_id, command),
            metadata={"tier": "important"}
        )
    )

    registry.register(
        Tool(
            name="read_file",
            description="Read file content from the sandboxed environment. Output length is limited.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute file path"}
                },
                "required": ["file_path"]
            },
            func=lambda file_path: container_service.read_file(account_id, file_path),
            metadata={"tier": "important"}
        )
    )

    registry.register(
        Tool(
            name="write_file",
            description="Write content to a file in the sandboxed environment. Missing files or directories will be created automatically.",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute file path"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["file_path", "content"]
            },
            func=lambda file_path, content: container_service.write_file(account_id, file_path, content),
            metadata={"tier": "important"}
        )
    )

    registry.register(
        Tool(
            name="run_python_script",
            description="Execute a Python script. Parameters must be a JSON object: "
            "{\"script_content\": \"<python code>\"}. "
            "script_content may include normal Python code, quotes, newlines, and indentation. "
            "The script will not automatically display the last expression value, so use print() to output results. "
            "For long scripts, prefer writing to a file and executing it.",
            parameters={
                "type": "object",
                "properties": {
                    "script_content": {"type": "string", "description": "Python script content. Include print() statements to output analysis results."}
                },
                "required": ["script_content"]
            },
            func=lambda script_content: _run_python_helper(container_service, account_id, script_content),
            metadata={"tier": "important"}
        )
    )

    # === Memory tools (if enabled for this account) ===
    account = get_account(db, account_id)
    if account and account.memory_enabled == "true":
        memory_add_tool, memory_search_tool = create_memory_tools(db, trace_id=trace_id)
        registry.register(memory_add_tool)
        registry.register(memory_search_tool)

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
