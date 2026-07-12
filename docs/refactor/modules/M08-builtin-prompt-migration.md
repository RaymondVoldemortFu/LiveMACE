# M08：内置 Prompt 文件化迁移

## 交付目标

把当前 Python 常量 Prompt 迁移为内置扩展资源，保持最终渲染文本逐字等价，不在本任务优化文案。

## 文件边界

- 读取/迁移：`backend/services/agent/prompts/*.py`、`backend/services/agent/rule_aware/prompts.py`、`llm_auditor.py` 中 Prompt。
- 新增：`backend/alpha_arena/builtin/prompts/`、`index.yaml`、Prompt golden fixtures。
- 修改 Agent 文件只允许将常量引用替换为 `PromptResolver.render()`。

## Prompt profile

至少建立：

- `core.react.default`
- `core.react.memory`
- `core.react.tool-routing`
- `core.multi-agent.default`
- `core.advanced-multi-agent.default`
- `core.rule-aware.default`
- `core.search-sub-agent.default`
- `core.compliance-audit.default`

profile 只描述多个 prompt id 的绑定，不复制内容。

## TODO

- [ ] 提取 system、manager、specialist、execution、reminder、audit Prompt 为独立文件。
- [ ] 将 `get_trade_agent_prompt()` 的条件 block 变成显式 profile/变量组合，输出保持一致。
- [ ] 将 `.format()` 变量列入 index，并为每个 Agent 建真实变量 fixture。
- [ ] 为迁移前 Python 函数和迁移后 renderer 建 golden text/hash 对比。
- [ ] 去除 Agent 对 Prompt 常量模块的 import；Prompt Python 文件最终删除或只留 deprecated re-export，M17 后删除 re-export。
- [ ] 账户 system-prompt API 改为通过 resolver 预览实际 Prompt，并返回 prompt id/version/hash。

## 验收

- 所有内置 Prompt golden comparison 逐字相同，包括空行和转义。
- 四类 Agent 与搜索/audit 不再直接 import Prompt 常量。
- 用户可复制内置 profile 到外部扩展，仅修改文件即可覆盖。
- Prompt 缺失在账户配置校验时失败，不在自动交易中 late crash。

## 前置与并行

前置 M07；不同 Prompt 家族可并行提取，index 与 profile 由一人合并。

