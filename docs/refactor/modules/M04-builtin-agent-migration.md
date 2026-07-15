# M04：内置 Agent 迁移

## 交付目标

将现有四类 Agent 注册为 `core.*` 组件，并保持其 Prompt、步骤、工具调用、终止和交易行为不变。

## 文件边界

- 修改：`backend/services/agent/react.py`、`multi_agent.py`、`multi_agent_advanced.py`、`rule_aware/*`、`factory.py`、`core.py`。
- 新增：`backend/alpha_arena/builtin/agents/` 及各 Agent config schema。
- 不修改工具实现、Prompt 文案、交易执行函数。

## 注册组件

| component id | 现有实现 | 默认配置必须保持 |
| --- | --- | --- |
| `core.react` | `ReActAgent` | `MAX_STEPS`、提醒阈值、tool routing |
| `core.multi-agent` | `MultiAgent` | manager + trading/news/coder 流程 |
| `core.advanced-multi-agent` | `AdvancedMultiAgent` | analyst/critic/execution 流程 |
| `core.rule-aware` | `RuleAwareAgent` | rule engine、audit 开关、reminder |

## TODO

- [ ] 为每类实现 `AgentFactory` adapter；旧类可保留在原文件，本任务不要求算法重写。
- [ ] 四类 Agent 对系统继续暴露同步 `run()`；不得为了迁移而改写成 async Agent。
- [ ] 保持现有 Agent 内部步骤与工具调用顺序；MultiAgent/AdvancedMultiAgent 的内部并发行为按当前实现原样保留，不由系统 Runtime 接管。
- [ ] 将 portfolio/prices dict adapter 放在内置层，第三方接口只见 `DecisionContext`。
- [ ] 将 on_step 转成 `RuntimeEvent`，消息内容和 step_number 保持。
- [ ] 统一生成 `AgentRunResult`；成功调用 execute_trade 后填 `executed_trades`。
- [ ] 无交易结果明确返回 HOLD/termination reason，不返回待执行 JSON。
- [ ] 将原 `agent_type` 值映射到 component id，只用于数据库迁移：`react`、`multi_agent`、`advanced_multi_agent`、`rule_aware`。
- [ ] 删除 factory 中按字符串 import/if-elif；只通过 registry 获取。
- [ ] 按 M00 fixture 比较迁移前后 tool call 顺序、Prompt hash、DB 副作用。

## 验收

- 四个 component 均可由 registry id 构建和运行。
- 四个 component 均在账户 worker 内同步返回完整结果，不返回 coroutine。
- 原有账户在迁移后选择同一实现，不需要用户手动编辑。
- characterization tests 对交易结果、终止原因和 trace 步骤通过。
- 生产代码不再调用 `create_agent(agent_type, ...)` 硬编码工厂。

## 前置与并行

前置 M03、M05、M07；四个内置 Agent adapter 可由四人并行，最后统一 registry registration。
