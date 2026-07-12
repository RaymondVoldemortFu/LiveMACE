# M16：Agent、Tool、Prompt 版本与 Trace 关联

## 交付目标

让每次决策可回答“使用了哪个扩展版本、哪个 Prompt hash、哪些工具、产生了什么交易”，但不改变评测指标和交易行为。

## 文件边界

- 修改：`database/models.py` 的 trace/decision 相关模型、`ai_decision_service.py` 迁移后的 trace writer、`api/agent_routes.py`。
- 新增：runtime event DTO、event sink adapter、trace repository/service。
- 不引入外部日志平台。

## Event 接口

```python
@dataclass(frozen=True)
class RuntimeEvent:
    event_type: str
    occurred_at: datetime
    account_id: int
    decision_round_id: str
    trace_id: str
    component: ExtensionRef | None
    payload: Mapping[str, JsonValue]
```

事件至少包括 run started/finished、prompt rendered、LLM call、tool called/finished、trade result、run failed。

## TODO

- [ ] 设计 event 到现有 `AgentTrace` 的兼容 adapter；不丢失 step_number/role/content/tool_calls/tool_output。
- [ ] 存储 agent id/version、tool id/version、prompt id/version/hash、termination reason、round id。
- [ ] 若需新表/列，建立 SQLite/MySQL migration 并保持旧 trace 可读。
- [ ] redaction 在持久化之前执行；API key、authorization header、敏感 config 永不写库。
- [ ] 对大 tool output 保留当前 LONGTEXT 能力和可配置截断摘要。
- [ ] Agent trace API 返回版本化 DTO，不直接暴露 ORM；旧字段保持前端可用。
- [ ] 结构化日志和 DB event 使用相同 event id，写 DB 失败不掩盖原 Agent 错误。

## 验收

- 一次 Agent run 可通过 trace API 还原组件版本、Prompt hash、tool call 和 trade ref。
- secret redaction 测试覆盖嵌套 dict、header、URL credential。
- 旧 trace fixture 仍能返回；新 trace 不改变前端现有步骤展示。
- decision_round_id 在 Agent、Tool cache、trade event 中一致。

## 前置与并行

前置 M10、M13、M19。可与前端扩展 UI 和 bootstrap 并行；M22 使用其数据。

