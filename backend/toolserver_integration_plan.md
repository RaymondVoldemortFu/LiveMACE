# ToolServer 与 Agent 架构整合方案

本文档面向当前后端结构，给出 `backend/services/agent/toolserver.py` 与现有 `env_wrapper.py`、Agent 工具体系的整合方案，重点关注：工具注册方式、调用路径、配置与安全隔离。

## 现状简述

- `ToolServer` 负责加载 ToolEnv 中的工具 `api.py` 并执行指定 API。
- `env_wrapper.py` 当前只注册本地工具（行情、账户、搜索子智能体、容器读写等）。
- Agent 的工具调用统一走 `ToolRegistry`（OpenAI Tool schema）。

## 整合目标（剪枝到 10^2 规模后的前提）

1. 将裁剪后的 ToolEnv（约 10^2 工具）通过 `ToolServer` 接入 Agent。
2. 保持现有工具/子智能体逻辑不受影响。
3. 简化注册与管理流程：允许一次性注册全部可用工具。
4. 保留配置化开关（白名单/黑名单），以便后续扩展。
5. 确保运行安全（避免任意路径导入、避免误删等）。

## 总体设计（适配 10^2 规模）

### 1) 新增 ToolEnv 适配层（推荐方案）

在 `services/agent` 下新增 `toolenv_adapter.py`，职责：

- 读取 ToolEnv 元数据（`tools/<category>/*.json`）并生成工具描述。
- 将 ToolEnv 工具映射为可被 `ToolRegistry` 注册的 Tool。
- 统一对外暴露为 `register_toolenv_tools(registry, ...)`。

核心思路：

```
ToolEnvAdapter
  - list_tools() -> [(category, tool_name, api_list)]
  - build_tool_schema(api) -> OpenAI Tool schema
  - build_tool_func(...) -> 调用 ToolServer.run_tool
```

### 2) Agent 工具注册层对接

在 `env_wrapper.py` 的 `register_default_tools` 中：

- 保持现有工具注册逻辑不变；
- 在末尾新增：`ToolEnvAdapter.register_toolenv_tools(registry, ...)`；
- 该函数内部完成：
  - 读取启用清单（白名单/黑名单，可为空）。
  - 对每个 API 生成 Tool 并注册。
  - 默认直接注册全部工具，避免额外动态加载逻辑。

### 3) 运行流程

1. LLM 触发 `toolenv::<category>.<tool>.<api>` 形式的工具调用。
2. `ToolRegistry` 调用对应 Tool 的 `func(...)`。
3. `ToolServer.run_tool` 解析参数、注入 RapidAPI key、执行 `api.py`。
4. 返回结果，必要时按 schema 裁剪。

## 具体接口设计建议

### 工具命名规范

为避免与现有工具重名，建议统一命名为：

```
toolenv.{category}.{tool}.{api}
```

示例：

```
toolenv.Search.google_search.complete_web_search
```

### 参数 schema 生成规则

`tools/<category>/<tool>.json` 的 `api_list` 含：

- `required_parameters`
- `optional_parameters`

建议将其映射为 OpenAI Tool parameters：

```
{
  "type": "object",
  "properties": {...},
  "required": [...]
}
```

若缺失类型信息，可退化为 `string`，或根据 `type` 进行简单映射（NUMBER -> number, STRING -> string, BOOLEAN -> boolean）。

### ToolServer 调用方式

Tool func 统一封装为：

```
def toolenv_call(**kwargs):
    request = ToolRequest(
        category=category,
        tool_name=tool,
        api_name=api_name,
        tool_input=kwargs,
        strip="none"
    )
    return toolserver.run_tool(request)
```

## 配置建议（10^2 规模）

### 必需配置

- `RAPIDAPI_KEY`：注入到 toolenv 请求中。

### 可选配置

- `TOOLENV_ROOT` / `TOOLENV_TOOLS_ROOT` / `TOOLENV_SCHEMA_ROOT`：路径可用绝对或相对。
- `TOOLENV_ENABLE_LIST`：启用工具白名单（可选）。
- `TOOLENV_DISABLE_LIST`：禁用工具黑名单（可选）。
- 若已剪枝至 10^2，可默认不配置白名单，直接全量注册。

## 安全性考虑

1. **路径约束**：ToolServer 仅允许在 `toolenv/tools` 下加载 `api.py`。
2. **白名单过滤**：当启用清单存在时，仅注册白名单工具。
3. **全量注册可接受**：10^2 规模下可以一次性注册全部工具，仍建议保留黑名单兜底。
4. **运行隔离**：建议为工具调用设置超时/重试机制（可在未来增强）。

## 渐进式落地步骤（剪枝后）

1. 实现 `ToolEnvAdapter`（读取 metadata → 生成 Tool schema）。
2. 在 `env_wrapper.register_default_tools` 增加 ToolEnv 注册。
3. 若已剪枝至 10^2，可先全量注册验证执行链路。
4. 如需进一步精细控制，再增加白名单/黑名单配置。
5. 未来扩容时再引入更严格的分组/按需注册。

## 剪枝流程建议（与本方案联动）

1. 使用 `toolenv_pruner_gui.py` 进行人工剪枝并保留备份。
2. 用 `toolenv_stats.py` 统计剪枝后的工具与 API 数量。
3. 在 `ToolEnvAdapter` 中默认全量注册，观察工具调用效果与响应质量。

## 兼容性说明

本方案不改变现有 `ToolServer`、`ToolRegistry` 结构，仅新增适配层。
即使 ToolEnv 逻辑关闭，现有 Agent 依旧可正常工作。


