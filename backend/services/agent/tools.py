"""Legacy Tool API retained as an M05-to-M06 transition adapter.

New extensions must import :mod:`benchmark.tools`.  The classes in this module
remain unchanged for existing built-in Agents until M06 migrates their tools.
"""

from collections.abc import Mapping, Sequence
from typing import Dict, Optional, Iterable, List

from benchmark.contracts import JsonValue, to_jsonable
from benchmark.tools import ToolContext as PublicToolContext
from benchmark.tools import ToolResult as PublicToolResult
from benchmark.tools import ToolRuntimeError as PublicToolRuntimeError
from benchmark.tools import ToolSpec as PublicToolSpec


_PUBLIC_TO_LEGACY_TOOL_NAMES = {
    "core.execute_trade": "execute_trade",
    "core.market_snapshot": "get_market_snapshot",
    "core.kline_history": "get_kline_history",
    "core.account_state": "get_account_state",
    "core.decision_history": "get_history_decisions",
    "core.memory_add": "memory_add",
    "core.memory_search": "memory_search",
    "core.search": "consult_search_agent",
}

# services/agent/tools.py
class Tool:
    def __init__(self, name, description, parameters, func, metadata: Optional[Dict] = None):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.func = func
        self.metadata = metadata or {}

    def __call__(self, **kwargs):
        return self.func(**kwargs)


class ToolRegistry:
    def __init__(self):
        self.tools = {}
        self.active_tool_names: Optional[List[str]] = None

    def register(self, tool: Tool):
        self.tools[tool.name] = tool

    def get(self, name):
        if name in self.tools:
            return self.tools[name]
        # 部分模型会返回带命名空间的名称（如 python_repl:run_python_script）
        if isinstance(name, str) and ":" in name:
            suffix = name.rsplit(":", 1)[-1]
            if suffix in self.tools:
                return self.tools[suffix]
        raise KeyError(name)

    def call(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> PublicToolResult:
        """Bridge the public ToolInvoker SPI to an old callable Tool.

        This method exists only while M03/M05 coexist with unmigrated built-in
        Agents and Tools. M06 replaces this registry with the public runtime.
        """

        if not isinstance(arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        legacy_name = _PUBLIC_TO_LEGACY_TOOL_NAMES.get(name, name)
        if legacy_name.startswith("core."):
            legacy_name = legacy_name.removeprefix("core.")
        try:
            tool = self.get(legacy_name)
        except KeyError:
            return PublicToolResult(
                ok=False,
                error_code="TOOL_NOT_FOUND",
                error_message=f"Tool not registered: {name}",
            )
        try:
            result = tool(**dict(arguments))
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception as exc:
            raise PublicToolRuntimeError(
                "Legacy Tool execution failed",
                code="TOOL_INVOKE_FAILED",
                details={"tool_name": name},
            ) from exc
        try:
            if isinstance(result, PublicToolResult):
                to_jsonable(result)
                return result
            return PublicToolResult(ok=True, value=to_jsonable(result))
        except (TypeError, ValueError) as exc:
            raise PublicToolRuntimeError(
                "Legacy Tool returned a non-JSON result",
                code="TOOL_OUTPUT_INVALID",
                details={"tool_name": name},
            ) from exc

    def set_active_tools(self, names: Optional[Iterable[str]]):
        if names is None:
            self.active_tool_names = None
            return
        # preserve order, remove duplicates
        seen = set()
        ordered = []
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
        self.active_tool_names = ordered

    def clear_active_tools(self):
        self.active_tool_names = None

    def _iter_tools(self, use_active: bool = True):
        if use_active and self.active_tool_names is not None:
            tools = [self.tools[name] for name in self.active_tool_names if name in self.tools]
        else:
            tools = list(self.tools.values())
        return sorted(tools, key=lambda t: t.name)

    @property
    def openai_tools(self):
        """转换为 OpenAI SDK 使用的工具 schema"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters
                }
            }
            for t in self._iter_tools(use_active=True)
        ]

    @property
    def openai_tools_all(self):
        """全量工具 schema（忽略 active 筛选）"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters
                }
            }
            for t in self._iter_tools(use_active=False)
        ]


class LegacyToolAdapter:
    """Wrap one old callable Tool behind the public synchronous Tool SPI."""

    def __init__(self, legacy_tool: Tool, spec: PublicToolSpec) -> None:
        if not isinstance(legacy_tool, Tool):
            raise TypeError("legacy_tool must be Tool")
        if not isinstance(spec, PublicToolSpec):
            raise TypeError("spec must be benchmark.tools.ToolSpec")
        self._legacy_tool = legacy_tool
        self._spec = spec

    @property
    def spec(self) -> PublicToolSpec:
        return self._spec

    def invoke(
        self,
        context: PublicToolContext,
        arguments: Mapping[str, JsonValue],
    ) -> PublicToolResult:
        result = self._legacy_tool(**dict(arguments))
        if isinstance(result, PublicToolResult):
            return result
        return PublicToolResult(ok=True, value=result)


class LegacyToolProviderAdapter:
    """Explicit M05 adapter; M06 replaces it with built-in providers."""

    def __init__(self, tools: Sequence[LegacyToolAdapter]) -> None:
        self._tools = tuple(tools)

    def list_tools(self) -> tuple[LegacyToolAdapter, ...]:
        return self._tools


__all__ = [
    "Tool",
    "ToolRegistry",
    "LegacyToolAdapter",
    "LegacyToolProviderAdapter",
]
