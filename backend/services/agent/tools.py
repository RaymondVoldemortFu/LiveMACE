# services/agent/tools.py
class Tool:
    def __init__(self, name, description, parameters, func):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.func = func

    def __call__(self, **kwargs):
        return self.func(**kwargs)


class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, tool: Tool):
        self.tools[tool.name] = tool

    def get(self, name):
        return self.tools[name]

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
            for t in self.tools.values()
        ]
