# Gemini API 消息转换流程

本文档详细说明在 Open Alpha Arena 项目中使用 Gemini 模型时，消息的发送、接收及转换流程。

## 架构概览

```
ReActAgent / SearchSubAgent
    ↓
LLMClient (llm_client.py)        ← 统一入口，自动检测模型类型
    ↓ (模型名含 "gemini")
GeminiClient (gemini_client.py)  ← Gemini 专用客户端
    ↓ HTTP POST (requests 库)
Gemini API
```

**非 Gemini 模型**（gpt、qwen、deepseek 等）走 OpenAI SDK 路径，不经过 GeminiClient。

## LLMClient 路由逻辑

**文件**: `backend/services/agent/llm_client.py`

```python
def __init__(self, model: str, api_key: str, base_url: str = None):
    if "gemini" in model.lower():
        self.client = GeminiClient(model=model, api_key=api_key, base_url=base_url)
        self.is_gemini = True
    else:
        self.client = OpenAI(api_key=api_key, base_url=...)
        self.is_gemini = False

def call(self, messages, tools=None):
    if self.is_gemini:
        return self.client.call(messages, tools)  # 返回 GeminiMessage
    else:
        response = self.client.chat.completions.create(...)
        return response.choices[0].message         # 返回 OpenAI message
```

所有调用方（`react.py`、`search_agent.py` 等）只需使用 `llm_client.call()`，无需关心底层模型类型。

## OpenAI vs Gemini 格式差异

| 特性 | OpenAI | Gemini |
|------|--------|--------|
| 工具调用 | `tool_calls` | `functionCall` |
| 工具响应 | `tool` role | `functionResponse` in `user` role |
| 特殊字段 | 无 | `thoughtSignature` (必需) |
| 助手角色 | `assistant` | `model` |
| 对话模式 | 灵活 | 严格 user-model 交替 |
| 并行工具调用签名 | 无 | 只有第一个 functionCall 有签名 |
| HTTP 端点 | `/chat/completions` | `/models/{model}:generateContent` |

### 为什么 tool 对应 user role？

Gemini 要求对话严格遵循 **user-model 交替** 模式：

```
user → model → user → model
```

工具执行结果被视为"外部输入"，因此归类为 `user` role：

```
1. user:  "查询航班"
2. model: {functionCall: check_flight}   ← AI 决定调用工具
3. user:  {functionResponse: "延误"}     ← 工具结果作为用户输入
4. model: "航班延误，建议..."
```

### 并行函数调用的 thoughtSignature 规则

根据 Google 官方文档：

> 如果模型在回答中生成并行函数调用，则 `thoughtSignature` **仅附加到第一个** `functionCall` 部分。同一响应中的后续 `functionCall` 部分将不包含签名。

```json
{
  "parts": [
    {
      "functionCall": {"name": "get_weather", "args": {...}},
      "thoughtSignature": "abc123..."  // 只有第一个有
    },
    {
      "functionCall": {"name": "get_time", "args": {}}
      // 没有 thoughtSignature，这是正确的
    }
  ]
}
```

## 第一轮：初始请求

### 1. Agent 准备消息 (OpenAI 格式)

**文件**: `backend/services/agent/react.py`

```python
messages = [
    {"role": "system", "content": "You are a trading agent..."},
    {"role": "user", "content": "Analyze BTC and make decision"}
]
```

### 2. LLMClient 路由到 GeminiClient

```python
llm = LLMClient(model="gemini-2.0-flash", api_key=..., base_url=...)
response = llm.call(messages, tools)
# → 自动调用 GeminiClient.call()
```

### 3. 转换消息格式

**文件**: `backend/services/agent/gemini_client.py`
**方法**: `_convert_messages_to_gemini()`

转换规则：
- `system` → `systemInstruction`（提取到顶层）
- `user` → `{"role": "user", "parts": [{"text": "..."}]}`
- `assistant` → `{"role": "model", "parts": [functionCall + thoughtSignature]}`
- 连续多个 `tool` → **合并**到同一个 `{"role": "user", "parts": [functionResponse, ...]}`

### 4. 转换工具定义

**方法**: `_convert_tools_to_gemini()`

```python
# OpenAI 格式
{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}

# Gemini 格式（去掉 type 和 function 包装）
{"name": "...", "description": "...", "parameters": {...}}
```

### 5. 构建并发送 HTTP 请求

```python
payload = {
    "systemInstruction": {"parts": [{"text": system_instruction}]},
    "contents": contents,
    "tools": [{"functionDeclarations": gemini_tools}],
    "generationConfig": {"temperature": 0.4, "maxOutputTokens": 4000}
}

url = f"{base_url}/models/{model}:generateContent"
headers = {"Authorization": f"Bearer {api_key}"}  # 代理服务
# 或 url += f"?key={api_key}"                      # Google 官方

response = requests.post(url, headers=headers, json=payload, timeout=30)
```

### 6. 解析响应，保存 thoughtSignature

```python
for part in candidate["content"]["parts"]:
    if "functionCall" in part:
        fc = part["functionCall"]
        thought_sig = part.get("thoughtSignature")  # 可能为 None（并行调用时后续无签名）

        tool_calls.append(GeminiToolCall(
            call_id=f"call_{fc['name']}_{len(tool_calls)}",
            name=fc["name"],
            arguments=json.dumps(fc.get("args", {})),
            thought_signature=thought_sig  # 存储到 GeminiToolCall
        ))
```

### 7. 返回 GeminiMessage（OpenAI 兼容格式）

```python
# GeminiMessage.model_dump() 输出：
{
    "role": "assistant",
    "content": "",
    "tool_calls": [
        {
            "id": "call_get_account_state_0",
            "type": "function",
            "function": {"name": "get_account_state", "arguments": "{}"},
            "_thought_signature": "abc123..."  # 存储在 tool_call 级别
        },
        {
            "id": "call_get_market_snapshot_1",
            "type": "function",
            "function": {"name": "get_market_snapshot", "arguments": "{...}"}
            # 无 _thought_signature（并行调用，正常）
        }
    ],
    "_gemini_thought_signatures": ["abc123..."]  # 消息级别备份
}
```

## 第二轮：工具执行后继续

### 8. Agent 执行工具并更新历史

```python
# 添加 assistant 消息（含 thoughtSignature）
messages.append(response.model_dump())

# 执行每个工具，添加 tool 消息
for tc in response.tool_calls:
    result = execute_tool(tc.function.name, ...)
    messages.append({
        "role": "tool",
        "name": tc.function.name,
        "content": json.dumps(result)
    })
```

此时消息历史：
```python
[
    {"role": "system", "content": "..."},
    {"role": "user", "content": "Analyze BTC"},
    {
        "role": "assistant",
        "tool_calls": [
            {"function": {"name": "get_account_state"}, "_thought_signature": "abc123..."},
            {"function": {"name": "get_market_snapshot"}}  # 无签名
        ],
        "_gemini_thought_signatures": ["abc123..."]
    },
    {"role": "tool", "name": "get_account_state", "content": "{...}"},
    {"role": "tool", "name": "get_market_snapshot", "content": "{...}"}
]
```

### 9. 再次转换为 Gemini 格式（关键）

**处理 assistant 消息**：恢复 thoughtSignature

```python
for idx, tc in enumerate(tool_calls):
    part = {"functionCall": {"name": ..., "args": ...}}

    # 优先从 tool_call 级别取，fallback 到消息级别
    ts = tc.get("_thought_signature") or \
         (msg_signatures[idx] if idx < len(msg_signatures) else None)

    if ts:
        part["thoughtSignature"] = ts  # 只有有签名的才加
    parts.append(part)
```

**处理 tool 消息**：连续多个合并为一个 user 消息

```python
# 遍历时收集所有连续的 tool 消息
function_responses = []
while i < len(messages) and messages[i].get("role") == "tool":
    tool_msg = messages[i]
    function_responses.append({
        "functionResponse": {
            "name": tool_msg.get("name"),
            "response": {"result": tool_msg.get("content", "")}
        }
    })
    i += 1
contents.append({"role": "user", "parts": function_responses})  # 合并为一条
```

**转换结果**：
```python
[
    {"role": "user", "parts": [{"text": "Analyze BTC"}]},
    {
        "role": "model",
        "parts": [
            {
                "functionCall": {"name": "get_account_state", "args": {}},
                "thoughtSignature": "abc123..."  # 恢复成功
            },
            {
                "functionCall": {"name": "get_market_snapshot", "args": {...}}
                # 无签名，正确
            }
        ]
    },
    {
        "role": "user",
        "parts": [
            {"functionResponse": {"name": "get_account_state", "response": {...}}},
            {"functionResponse": {"name": "get_market_snapshot", "response": {...}}}
            # 多个 functionResponse 合并到同一个 user 消息
        ]
    }
]
```

## thoughtSignature 完整生命周期

```
Gemini 返回响应
    ↓
part.get("thoughtSignature")  → 存入 GeminiToolCall.thought_signature
    ↓
GeminiToolCall.model_dump()   → 序列化为 tool_call["_thought_signature"]
    ↓
GeminiMessage.model_dump()    → 同时存入 "_gemini_thought_signatures" 数组（备份）
    ↓
messages.append(response.model_dump())  → 保存到对话历史
    ↓
下次调用 _convert_messages_to_gemini()
    ↓
tc.get("_thought_signature") or msg_signatures[idx]  → 恢复签名
    ↓
part["thoughtSignature"] = ts  → 添加回 functionCall
    ↓
发送回 Gemini
```

## SearchSubAgent 的 Gemini 兼容

**文件**: `backend/services/agent/sub_agents/search_agent.py`

SearchSubAgent 同样通过 `LLMClient` 调用，已兼容 Gemini：

```python
# 使用 LLMClient.call() 而不是直接调用 OpenAI SDK
msg = self.llm_client.call(messages, tools)

# 兼容 OpenAI object 和 Gemini dict 两种格式的 tool_calls
tool_calls = msg.tool_calls if hasattr(msg, 'tool_calls') else (msg_dict.get('tool_calls') or [])
for tc in tool_calls:
    if isinstance(tc, dict):
        func_name = tc.get('function', {}).get('name')  # Gemini dict 格式
        tc_id = tc.get('id')
        tc_arguments = tc.get('function', {}).get('arguments', '{}')
    else:
        func_name = tc.function.name  # OpenAI object 格式
        tc_id = tc.id
        tc_arguments = tc.function.arguments
```

## 常见问题

### Q: 为什么不使用 google-generativeai SDK？

使用 `requests` 直接发送 HTTP 请求的优势：
- 支持自定义 `base_url`（代理/中转服务）
- 完全控制请求格式，可处理 `thoughtSignature` 等特殊字段
- 不依赖 SDK 版本更新

### Q: thoughtSignature 丢失会怎样？

Gemini API 返回 400 错误：
```
Function call is missing a thought_signature in functionCall parts.
This is required for tools to work correctly.
```

### Q: 并行函数调用时为什么只有第一个有签名？

这是 Google 的设计规范。只有第一个 `functionCall` 携带 `thoughtSignature`，后续的不携带。我们的实现正确处理了这种情况：只在有签名时才添加 `thoughtSignature` 字段。

### Q: 为什么多个 functionResponse 要合并到一个 user 消息？

Gemini 要求严格的 user-model 交替模式。如果每个 `functionResponse` 都是独立的 user 消息，会破坏这个交替结构，导致 API 报错。

## 相关文件

| 文件 | 作用 |
|------|------|
| `backend/services/agent/gemini_client.py` | Gemini 客户端，消息转换核心逻辑 |
| `backend/services/agent/llm_client.py` | 统一 LLM 入口，自动路由 Gemini/OpenAI |
| `backend/services/agent/react.py` | ReAct Agent 主循环 |
| `backend/services/agent/sub_agents/search_agent.py` | Search Sub-Agent，已兼容 Gemini |
| `backend/api/account_routes.py` | 测试连接 API，使用 LLMClient |

