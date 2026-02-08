from typing import Dict, Optional, Iterable, List

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
        return self.tools[name]

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
        if use_active and self.active_tool_names:
            return [self.tools[name] for name in self.active_tool_names if name in self.tools]
        return list(self.tools.values())

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
