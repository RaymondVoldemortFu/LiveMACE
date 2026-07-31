from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

from benchmark.contracts import SideEffect, ToolContext, ToolSpec
from benchmark.tools import ToolInvoker
from services.agent.tools import (
    LegacyToolAdapter,
    LegacyToolProviderAdapter,
    Tool,
    ToolRegistry as LegacyToolRegistry,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
TOOL_ROOT = BACKEND_ROOT / "benchmark" / "tools"


def test_public_tool_runtime_has_no_application_imports():
    forbidden = {
        "api",
        "database",
        "repositories",
        "services",
        "fastapi",
        "sqlalchemy",
        "redis",
        "docker",
    }
    violations = []
    for path in TOOL_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".", 1)[0] in forbidden:
                    violations.append(f"{path.name}: {name}")
    assert violations == []


def test_public_tool_import_has_no_application_side_effects():
    script = """
import json, sys
import benchmark.tools
forbidden = sorted(name for name in sys.modules if name.split('.', 1)[0] in {'api','database','services','repositories','fastapi','sqlalchemy','redis','docker'})
print(json.dumps(forbidden))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "[]"


def test_legacy_adapter_preserves_callable_result_without_changing_old_registry():
    old = Tool(
        name="legacy_echo",
        description="legacy",
        parameters={"type": "object"},
        func=lambda value: {"value": value},
    )
    adapter = LegacyToolAdapter(
        old,
        ToolSpec(
            name="core.legacy_echo",
            description="legacy",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect=SideEffect.READ_ONLY,
        ),
    )
    provider = LegacyToolProviderAdapter((adapter,))
    context = ToolContext(1, "round", "trace", "call", frozenset())

    assert provider.list_tools() == (adapter,)
    assert adapter.invoke(context, {"value": 7}).value == {"value": 7}


def test_legacy_registry_is_an_explicit_public_tool_invoker_bridge():
    registry = LegacyToolRegistry()
    calls = []
    registry.register(
        Tool(
            name="execute_trade",
            description="legacy trade",
            parameters={"type": "object"},
            func=lambda **arguments: calls.append(arguments) or {"executed": True},
        )
    )

    assert isinstance(registry, ToolInvoker)
    result = registry.call("core.execute_trade", {"operation": "hold"})
    missing = registry.call("com.example.missing", {})

    assert result.ok is True
    assert result.value == {"executed": True}
    assert calls == [{"operation": "hold"}]
    assert missing.error_code == "TOOL_NOT_FOUND"
