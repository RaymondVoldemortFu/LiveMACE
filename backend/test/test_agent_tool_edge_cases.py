"""生产环境边界：Gemini 工具 schema、命名空间工具名、execute_trade 宽松数值。"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.agent.llm_client import LLMClient
from services.agent.tools import Tool, ToolRegistry
from services.agent.trade_execution_tool import _parse_float_loose, _parse_int_loose


def test_sanitize_openai_tools_for_gemini_collapses_union_type():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "demo_api",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "q": {"type": ["number", "string"], "description": "inferred"},
                    },
                },
            },
        }
    ]
    out = LLMClient._sanitize_openai_tools_for_gemini(tools)
    assert out[0]["function"]["parameters"]["properties"]["q"]["type"] == "number"
    assert tools[0]["function"]["parameters"]["properties"]["q"]["type"] == ["number", "string"]


def test_tool_registry_get_strips_namespace_prefix():
    reg = ToolRegistry()
    reg.register(Tool("run_python_script", "run", {"type": "object", "properties": {}}, lambda **_: {}))
    assert reg.get("python_repl:run_python_script").name == "run_python_script"


def test_parse_float_loose_strips_xml_suffix():
    raw = (
        '0.15</parameter>\n<parameter name="size_mode">portion</parameter>\n'
    )
    assert _parse_float_loose(raw) == 0.15
    assert _parse_int_loose("12</foo>", default=1) == 12
