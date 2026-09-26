# 提案：将 reasoning_effort / extra_body 接入账户级 LLM 配置

- 状态：草案（待团队讨论）
- 关联代码：`backend/services/agent/llm_client.py`、`backend/eval/offline_llm_audit.py`、`backend/services/ai_decision_service.py`
- 讨论目标：是否（以及如何）让每个交易账户可以按模型配置推理强度（reasoning effort）与厂商私有请求体扩展（extra_body）

## 1. 背景与现状

`LLMClient` 已支持两个可选构造参数（commit `edd972d` 为离线评测引入）：

- `reasoning_effort: Optional[str]`：显式配置时以 OpenAI Chat Completions 原生顶层参数 `reasoning_effort` 注入请求；不配置（`None`）时请求体中完全不出现该字段，由厂商默认策略决定。
- `extra_body: Optional[dict]`：透传 OpenAI 兼容网关的私有请求体扩展（如 Qwen 的 `enable_thinking`、DeepSeek 的 `thinking.type`）。

当前接入范围仅限**离线评测路径**（`eval/offline_llm_audit.py`，通过 CLI 参数 / 环境变量解析后传入）。**在线交易路径**（`services/ai_decision_service.py` 按账户构造 `LLMClient`）尚未接入：`accounts` 表只有 `model` / `base_url` / `api_key` 三个 LLM 相关字段，无法按账户控制推理强度。

## 2. 厂商差异事实表

各厂商对"推理强度"的参数定义不统一，这是本提案的核心约束：

| 厂商 / 网关风格 | 参数形态 | 值域 | 备注 |
|---|---|---|---|
| OpenAI（o1/o3/o4/gpt-5 系） | 顶层 `reasoning_effort` | `none` / `minimal` / `low` / `medium` / `high` | 官方原生定义 |
| xAI（grok-3-mini） | 顶层 `reasoning_effort` | `low` / `high` | grok-4 不支持，传入会 400 |
| Gemini（OpenAI 兼容端点） | 顶层 `reasoning_effort`（内部映射 thinking budget） | `low` / `medium` / `high` / `none` | 取决于网关实现 |
| Qwen / DashScope | `extra_body.enable_thinking` | bool | 非 effort 语义，仅开关 |
| vLLM Qwen 网关 | `extra_body.chat_template_kwargs.enable_thinking` | bool | 同上 |
| DeepSeek | `extra_body.thinking.type` | `enabled` / `disabled` | 同上 |
| 不支持推理参数的模型 | —— | —— | 传顶层 `reasoning_effort` 可能 400 或被静默忽略 |

## 3. 设计原则（已在离线路径验证）

1. **`LLMClient` 只透传、不翻译。** 不在客户端内按模型名自动改写参数：模型名经网关后不可靠（自定义命名常见）、各家值域不同、静默改写会把配置错误掩盖成"看起来生效了"。配置错误应当由厂商侧如实报错并暴露给使用者。
2. **顶层 `reasoning_effort` 只服务于 OpenAI 形态参数。** 凡是厂商声明支持该形态的（OpenAI / xAI / Gemini 兼容端点），直接配置该字段；值域校验交给厂商。
3. **非 OpenAI 形态的 thinking 控制走 `extra_body`。** Qwen / DeepSeek / vLLM 等的私有开关不折叠进 `reasoning_effort` 语义。风格映射（`thinking_param_style`：`openai` / `deepseek` / `vllm` / `qwen`）保留在配置解析层（现为 `eval/offline_llm_audit.py`），不下沉到 `LLMClient`。
4. **默认不注入任何字段。** 两者都不配置时请求体不含 thinking / effort 相关字段，行为完全等同于历史版本，也是对不支持该参数的模型最安全的姿态。该行为已有测试锁定（`tests/agents/test_llm_client_audit_options.py`）。

## 4. 提议的账户级接入方案

若团队同意扩大作用范围，建议按以下增量实施：

### 4.1 数据模型

`accounts` 表新增两个可空列（与 `model` / `base_url` / `api_key` 并列）：

- `reasoning_effort VARCHAR(20) NULL`：仅存放 OpenAI 形态值；`NULL` 表示跟随厂商默认。
- `llm_extra_body TEXT NULL`：JSON 对象文本，透传给 `LLMClient(extra_body=...)`；`NULL` 表示不注入。

需要一条 startup migration（沿用 `database/migrations_startup.py` 的 `information_schema` 幂等判断模式）。

### 4.2 后端接线

- `services/ai_decision_service.py` 构造 `LLMClient` 时读取上述两列并传入。
- 账户创建 / 更新 API（`api/account_routes.py`）接受这两个可选字段；`llm_extra_body` 在入口做一次 `json.loads` 校验（必须是 JSON object），拒绝非法输入而非静默丢弃。
- `reasoning_effort` 的值域校验建议只做形态归一（trim / lower），不做白名单硬校验——各厂商值域不同，白名单会阻碍新值（参考 `eval/offline_llm_audit.py` 中 `_normalize_reasoning_effort` 的取舍，可讨论）。

### 4.3 前端

账户设置表单增加两个可选输入：推理强度（下拉 + 自定义）与高级 JSON 扩展（默认折叠）。

### 4.4 明确不做的事

- 不做"按模型名猜默认 effort"。
- 不在发现厂商报错（如 grok-4 收到 `reasoning_effort` 返回 400）时自动降级重试——配置错误应暴露给用户修正，而不是被兜底掩盖。

## 5. 风险与开放问题

1. **配置错误的暴露方式**：厂商 400 会导致该账户当轮决策失败。是否需要在账户"测试连接"入口（`test_llm_connection`）中带上这两个配置做预检？（建议：是，成本低且能提前暴露问题。）
2. **`llm_extra_body` 的安全边界**：透传任意 JSON 意味着用户可注入任意请求体字段（如覆盖 `temperature`）。是否需要 key 黑名单？（建议：暂不需要，账户 API key 本就属于用户自己。）
3. **`thinking_param_style` 是否也要账户化**：当前提案让用户直接写 `extra_body` 原始 JSON，不提供 style 快捷方式。如果团队希望降低配置门槛，可把离线评测的 style 解析器提为共享模块。
4. **值域校验策略**：见 4.2，硬白名单 vs 仅归一化，需要拍板。

## 6. 范围外（已单独修复）

`test/test_account_llm_connection.py` 中 7 处手工装配（`object.__new__(LLMClient)`）未随 `edd972d` 同步补 `reasoning_effort` / `extra_body` 属性，导致 `call()` 抛 `AttributeError`。这是测试装配腐化而非运行时缺陷（线上构造均走 `__init__`），已通过补齐测试装配修复，不依赖本提案的结论。
