# API Server & Tools Schema

## 概述

- **api_server.py**：通过动态加载 `apis/<name>/api.py` 提供统一 HTTP 接口，自动忽略 `unsupported_apis.UNSUPPORTED_APIS` 中的 API。
- **test_apis.py**：遍历所有可用 API，调用 `run({})` 并校验返回结构，用于确认可用性。
- **tools_schema.json**：与 OpenAI / ToolRegistry 兼容的 JSON 描述（`type: function`, `name`, `description`, `parameters`）。

## 启动服务

```bash
pip install -r requirements.txt
python api_server.py
# 监听 http://0.0.0.0:5000
```

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /v1 | 返回可用 API 列表 `{"apis": ["acronymexpander", ...]}` |
| GET | /v1/tools | 返回 OpenAI 风格 tools schema（与 tools_schema.json 一致） |
| POST | /v1/<name> | 调用指定 API，请求体 JSON 即 `params`，返回 `run(params)` 结果 |
| GET | /v1/<name>?key=val | 同上，params 由 query 组成 |

响应格式：`{"status": "ok"|"error", "error": null|"...", "data": ...}`

## 测试所有可用 API

```bash
python test_apis.py
# 遍历所有非 unsupported 的 API，空参调用并检查返回结构
```

## 生成 tools_schema.json

```bash
python scripts/build_tools_schema.py
# 根据当前可用 API 列表与 openapi.json 描述重新生成 tools_schema.json
```

## 与 ToolRegistry 兼容

`tools_schema.json` 与如下定义兼容：

```python
# 例如 services/agent/tools.py
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
```

在本地用 API Server 的实现在内存中挂载到 Registry：

```python
from services.agent.tools import Tool, ToolRegistry
from api_server import build_tool_registry

registry = build_tool_registry(Tool, ToolRegistry)
# registry.openai_tools 与 tools_schema.json 结构一致
result = registry.get("moonposition")(lat=40, lon=-74)
```

或直接使用静态 JSON：读取 `tools_schema.json` 作为 `openai_tools`，调用时通过 HTTP POST `/v1/<name>` 转发参数即可。
