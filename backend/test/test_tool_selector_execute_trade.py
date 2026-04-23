"""tool-selector 必选工具须含 execute_trade（路由模式初始暴露与合并逻辑）。"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.agent.tool_selector import REQUIRED_TOOL_NAMES, _apply_tool_selection
from services.agent.tools import Tool, ToolRegistry


def test_required_tool_names_includes_execute_trade():
    assert "execute_trade" in REQUIRED_TOOL_NAMES


def _minimal_registry_with_required() -> ToolRegistry:
    reg = ToolRegistry()
    for name in REQUIRED_TOOL_NAMES:
        reg.register(
            Tool(
                name=name,
                description=f"stub {name}",
                parameters={"type": "object", "properties": {}},
                func=lambda **_: {},
            )
        )
    reg.register(
        Tool(
            name="some_optional_api",
            description="optional",
            parameters={"type": "object", "properties": {}},
            func=lambda **_: {},
        )
    )
    return reg


def test_apply_tool_selection_always_keeps_execute_trade_without_llm_pick():
    reg = _minimal_registry_with_required()
    combined = _apply_tool_selection(
        reg,
        ["some_optional_api"],
        include_meta=False,
    )
    assert reg.active_tool_names == combined
    assert combined[: len(REQUIRED_TOOL_NAMES)] == list(REQUIRED_TOOL_NAMES)
    assert "execute_trade" in combined
