"""Built-in ToolProvider construction, legacy names, and register_default_tools."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark.builtin.tools._support import legacy_tool_name, make_tool_context
from benchmark.builtin.tools.account import AccountToolsProvider
from benchmark.builtin.tools.history import HistoryToolsProvider
from benchmark.builtin.tools.legacy import as_legacy_tool
from benchmark.builtin.tools.market import MarketToolsProvider
from benchmark.builtin.tools.memory import MemoryToolsProvider
from benchmark.builtin.tools.public_api import PublicApiToolsProvider
from benchmark.builtin.tools.sandbox import SandboxToolsProvider
from benchmark.builtin.tools.search import SearchToolsProvider
from services.agent.tools import ToolRegistry


BACKEND_ROOT = Path(__file__).resolve().parents[2]
BUILTIN_TOOL_ROOT = BACKEND_ROOT / "benchmark" / "builtin" / "tools"


def test_builtin_tool_modules_do_not_import_fastapi_or_trade_executors():
    forbidden_roots = {"fastapi"}
    forbidden_modules = {
        "services.order_matching",
        "services.order_executor_leverage",
        "services.trading_commands",
    }
    violations = []
    for path in BUILTIN_TOOL_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".", 1)[0]
                if root in forbidden_roots or name in forbidden_modules:
                    violations.append(f"{path.name}: {name}")
    assert violations == []


def test_each_non_trading_provider_can_be_constructed_without_services():
    context = make_tool_context(account_id=7, trace_id="trace-tools")
    market = MarketToolsProvider(
        get_price=lambda symbol, market: 100.0,
        get_status=lambda symbol, market: {"open": True},
        get_klines=lambda *args, **kwargs: [{"open": 1}],
        sandbox=SimpleNamespace(write_file=lambda *args, **kwargs: "Success"),
    )
    account = AccountToolsProvider(
        account_reader=lambda account_id: {"account": {"id": account_id}, "positions": []},
        history_reader=lambda account_id, limit: [{"account_id": account_id, "limit": limit}],
    )
    history = HistoryToolsProvider(
        history_reader=lambda account_id, limit: [{"limit": limit}],
    )
    search = SearchToolsProvider(
        search_runner=lambda query, topic, time_range, max_results: {"query": query}
    )
    sandbox = SandboxToolsProvider(
        sandbox=SimpleNamespace(
            execute_command=lambda account_id, command: (0, command),
            read_file=lambda account_id, file_path: "ok",
            write_file=lambda account_id, file_path, content: "Success",
        )
    )
    memory = MemoryToolsProvider(
        add_fn=lambda context, arguments: {"stored": True},
        search_fn=lambda context, arguments: [],
    )
    public = PublicApiToolsProvider(limit=1)

    providers = (market, account, history, search, sandbox, memory, public)
    for provider in providers:
        tools = provider.list_tools()
        assert tools
        for tool in tools:
            assert tool.spec.name.startswith(("core.", "public."))
            assert legacy_tool_name(tool.spec.name)

    snapshot = next(tool for tool in market.list_tools() if tool.spec.name == "core.market_snapshot")
    result = snapshot.invoke(context, {"symbol": "BTC", "market": "CRYPTO"})
    assert result.ok is True
    assert result.value["price"] == 100.0

    state = next(tool for tool in account.list_tools() if tool.spec.name == "core.account_state")
    assert state.invoke(context, {}).value["account"]["id"] == 7

    history_tool = history.list_tools()[0]
    assert history_tool.invoke(context, {"limit": 3}).value == [{"limit": 3}]
    with pytest.raises(ValueError):
        history_tool.invoke(context, {"limit": "all"})

    search_tool = search.list_tools()[0]
    assert search_tool.invoke(context, {"query": "btc"}).value == {"query": "btc"}
    with pytest.raises(ValueError):
        search_tool.invoke(context, {"query": "btc", "max_results": "many"})

    shell = next(
        tool for tool in sandbox.list_tools() if tool.spec.name == "core.execute_shell_command"
    )
    assert shell.invoke(context, {"command": "echo hi"}).value == [0, "echo hi"]

    add_tool = next(tool for tool in memory.list_tools() if tool.spec.name == "core.memory_add")
    assert add_tool.invoke(
        context,
        {"experience": "rule", "account_id": "7", "market": "CRYPTO"},
    ).value == {"stored": True}

    assert public.list_tools()[0].spec.name.startswith("public.")
    assert getattr(public.list_tools()[0], "source") == "public-apis"


def test_legacy_adapter_preserves_public_tool_names_and_schemas():
    provider = AccountToolsProvider(
        account_reader=lambda account_id: {"id": account_id},
        history_reader=lambda account_id, limit: [],
    )
    context = make_tool_context(account_id=3, trace_id="legacy")
    for tool in provider.list_tools():
        legacy = as_legacy_tool(tool, account_id=3, trace_id="legacy")
        assert legacy.name == legacy_tool_name(tool.spec.name)
        assert legacy.description == tool.spec.description
        assert legacy.parameters == dict(tool.spec.input_schema)
        public_result = tool.invoke(context, {})
        legacy_result = legacy.func()
        assert legacy_result == public_result.value


def test_kline_write_failure_returns_tool_result_error_with_metadata():
    class FailingSandbox:
        def write_file(self, account_id, file_path, content):
            del account_id, content
            return f"failed:{file_path}"

    provider = MarketToolsProvider(
        get_klines=lambda *args, **kwargs: [{"open": 1}, {"open": 2}],
        sandbox=FailingSandbox(),
    )
    tool = next(item for item in provider.list_tools() if item.spec.name == "core.kline_history")
    result = tool.invoke(
        make_tool_context(account_id=9, trace_id="kline"),
        {"symbol": "BTC", "market": "CRYPTO"},
    )
    assert result.ok is False
    assert result.error_code == "KLINE_WRITE_FAILED"
    assert result.metadata["source"] == "market_kline"
    assert result.metadata["row_count"] == 2
    assert str(result.metadata["file_path"]).startswith("/workspace/kline_BTC_")


def test_legacy_kline_write_failure_returns_error_message_only():
    class FailingSandbox:
        def write_file(self, account_id, file_path, content):
            del account_id, content
            return "disk full"

    provider = MarketToolsProvider(
        get_klines=lambda *args, **kwargs: [{"open": 1}],
        sandbox=FailingSandbox(),
    )
    tool = next(item for item in provider.list_tools() if item.spec.name == "core.kline_history")
    payload = as_legacy_tool(tool, account_id=9, trace_id="kline").func(
        symbol="BTC",
        market="CRYPTO",
    )
    assert payload == {"error": "Failed to save K-line data to container: disk full"}


def test_public_api_provider_rejects_invalid_and_duplicate_names(monkeypatch):
    from benchmark.builtin.tools import public_api as public_api_module

    monkeypatch.setattr(
        public_api_module,
        "_load_schema_entries",
        lambda: [
            {
                "legacy_name": "Not A Valid Name",
                "description": "bad",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )
    with pytest.raises(ValueError, match="not a valid identifier"):
        PublicApiToolsProvider()

    monkeypatch.setattr(
        public_api_module,
        "_load_schema_entries",
        lambda: [
            {
                "legacy_name": "alpha",
                "description": "one",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "legacy_name": "alpha",
                "description": "two",
                "parameters": {"type": "object", "properties": {}},
            },
        ],
    )
    with pytest.raises(ValueError, match="duplicate public API tool name"):
        PublicApiToolsProvider()

    monkeypatch.setattr(public_api_module, "_load_schema_entries", lambda: [])
    with pytest.raises(ValueError, match="produced no tools"):
        PublicApiToolsProvider()


def test_memory_provider_zero_arg_construction_does_not_open_a_session(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("MemoryToolsProvider must not open a session at construction")

    monkeypatch.setattr("database.connection.SessionLocal", explode)
    provider = MemoryToolsProvider()
    names = [tool.spec.name for tool in provider.list_tools()]
    assert names == ["core.memory_add", "core.memory_search"]


def test_register_default_tools_omits_memory_when_disabled(monkeypatch):
    from services.agent import env_wrapper

    account = SimpleNamespace(
        model="test-model",
        api_key="encrypted-placeholder",
        base_url=None,
        name="test-agent",
        memory_enabled="false",
    )
    monkeypatch.setattr(env_wrapper, "get_account", lambda db, account_id: account)
    monkeypatch.setattr(env_wrapper, "list_positions", lambda db, account_id: [])
    monkeypatch.setattr(env_wrapper, "ContainerService", lambda: SimpleNamespace())
    monkeypatch.setattr(
        env_wrapper,
        "SearchSubAgent",
        lambda **kwargs: SimpleNamespace(run=lambda *args: {}),
    )

    disabled = ToolRegistry()
    env_wrapper.register_default_tools(disabled, SimpleNamespace(), account_id=7, trace_id="t")
    assert "memory_add" not in disabled.tools
    assert "memory_search" not in disabled.tools
    assert "get_account_state" in disabled.tools
    assert "get_market_snapshot" in disabled.tools
    assert "get_kline_history" in disabled.tools
    assert "consult_search_agent" in disabled.tools
    assert "execute_shell_command" in disabled.tools
    assert "read_file" in disabled.tools
    assert "write_file" in disabled.tools
    assert "run_python_script" in disabled.tools
    assert "execute_trade" in disabled.tools

    account.memory_enabled = "true"

    class _MemoryTool:
        def __init__(self):
            self.func = lambda **kwargs: {"ok": True}

    monkeypatch.setattr(
        "services.agent.memory_tools.create_memory_tools",
        lambda db, **kwargs: (_MemoryTool(), _MemoryTool()),
    )
    enabled = ToolRegistry()
    env_wrapper.register_default_tools(enabled, SimpleNamespace(), account_id=7, trace_id="t")
    assert "memory_add" in enabled.tools
    assert "memory_search" in enabled.tools
