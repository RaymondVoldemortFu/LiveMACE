# LiveMACE bench 重构方案说明

这份文档用于团队内部对齐本轮重构。更细的接口定义和任务拆分见：

- [公共扩展接口规范](000-public-interface-spec.md)
- [模块级任务索引](README.md)
- [模块分组与并行开发计划](module-groups.md)

## 1. 我们为什么要做这次重构

现在系统已经支持 ReAct、多 Agent、Rule-aware、工具调用、Prompt、记忆和沙箱，但新增或替换这些能力通常需要阅读并修改核心代码。

例如：

- 新增 Agent，需要修改 `factory.py` 的判断分支；
- 新增工具，需要进入 `env_wrapper.py` 注册；
- 修改 Prompt，需要编辑 Python 常量，并理解各 Agent 如何拼装 Prompt；
- Agent 想交易，需要理解 `execute_trade_tool.py`、订单撮合和杠杆执行的关系；
- 账户只保存 `agent_type` 和几个开关，无法完整表达“使用哪个 Agent、哪些工具、哪个 Prompt”。

这对开源项目不够友好。使用者应该只需要看扩展文档和公开接口，而不是先读完整个后端。

本次重构希望做到：

1. Agent、工具和 Prompt 可以通过配置选择或替换；
2. 第三方可以按照公开接口编写扩展；
3. 内置功能和第三方扩展使用同一套注册方式；
4. 交易、资金、持仓和风控仍由系统核心控制；
5. 重构过程中不改变现有功能和交易结果。

## 2. 重构后的整体调用方式

当前主要调用关系是：

```text
ai_decision_service
  -> 根据 agent_type 创建 Agent
  -> env_wrapper 注册工具
  -> Agent 引用 Python Prompt 常量
  -> execute_trade_tool 执行交易
```

重构后变为：

```text
账户运行配置
  -> Extension Catalog 查找组件
  -> Agent Registry 创建 Agent
  -> Prompt Registry 提供 Prompt
  -> Tool Registry 提供工具
  -> Agent Runtime 运行 Agent
  -> Tool Invoker 执行工具
  -> Trade Command Gateway 执行交易
  -> 现有订单、成交、持仓和资金逻辑
```

可以简单理解为：

- Catalog 负责回答“系统中有哪些组件”；
- Registry 负责回答“如何创建或找到这个组件”；
- Runtime 负责运行 Agent 和工具；
- Gateway 负责守住交易入口；
- 账户配置负责决定实际使用哪些组件。

## 3. 扩展配置文件

每个扩展目录都包含一个固定名称的文件：

```text
alpha-arena-extension.yaml
```

一个同时提供 Agent、工具和 Prompt 的扩展示例：

```yaml
api_version: 1
id: com.example.my-extension
version: 1.0.0
name: My Extension
description: Example Agent and tools

python:
  requires: ">=3.10"
  entrypoint: "my_extension:extension"

components:
  agents:
    - id: com.example.my-agent
      factory: "my_extension.agents:create_factory"
      config_schema: "schemas/agent.schema.json"

  tools:
    - provider: "my_extension.tools:create_provider"

  prompts:
    - directory: "prompts"
      index: "prompts/index.yaml"

capabilities:
  requested:
    - market.read
    - sandbox.write
```

系统启动时会：

1. 找到扩展目录；
2. 检查配置格式和接口版本；
3. 检查文件路径、组件 ID 和权限；
4. 只加载配置中声明的 Python 入口；
5. 注册 Agent、工具和 Prompt；
6. 完成注册后冻结 Catalog，再启动自动交易。

扩展加载失败不会影响其他扩展。但如果某个账户正在引用失败的扩展，该账户会被标记为配置无效，不会偷偷换成另一个 Agent。

### 只修改 Prompt 时不需要写 Python

Prompt-only 扩展可以只有配置和文本文件：

```text
my-prompts/
  alpha-arena-extension.yaml
  prompts/
    index.yaml
    react-system.txt
```

这样可以让 Prompt 调整与后端代码修改分开。

## 4. Agent 接口规定

第三方 Agent 只需要实现一个主要接口：

```python
class Agent(Protocol):
    def run(
        self,
        context: DecisionContext,
    ) -> AgentRunResult:
        ...
```

`DecisionContext` 提供 Agent 本轮需要的只读信息：

- 账户 ID；
- 决策轮次 ID；
- Trace ID；
- 账户和持仓快照；
- 当前价格；
- Agent 自己的配置；
- 本轮开始时间。

Agent 不会得到：

- SQLAlchemy Session；
- ORM Account、Position、Order；
- 数据库连接；
- FastAPI Request；
- Redis client；
- 订单撮合服务。

Agent 如果需要数据或执行动作，只能调用已经授权的工具。

系统会通过线程池同时运行不同账户的 Agent，但对单个 Agent 来说，`run()` 是同步接口。Agent 的一次 LLM 调用、工具调用和交易调用完成后，才继续下一步。

返回值只描述这次运行的结果：

```python
@dataclass(frozen=True)
class AgentRunResult:
    trace_id: str
    decision_round_id: str
    termination_reason: TerminationReason
    executed_trades: tuple[ExecutedTradeRef, ...]
    summary: str = ""
```

这里的 `executed_trades` 是已经执行过的交易引用，不是等待上层执行的交易计划。

## 5. 为什么删除旧 JSON 决策接口

当前系统有两种不同的执行方式：

```text
旧方式：LLM 返回 JSON -> 上层解析 JSON -> 上层执行交易

工具方式：Agent 调用 execute_trade -> 工具内已经执行交易
```

两种方式返回的数据比较相似，但一个是“待执行”，另一个是“已执行”。这使上层必须通过 `protocol` 或 `executed_trades` 判断是否需要再次下单，容易产生重复交易。

重构后只保留工具方式：

```text
需要交易
  -> Agent 调用 core.execute_trade
  -> 工具返回执行成功或拒绝结果

不需要交易
  -> Agent 返回 HOLD
```

因此会删除：

- `call_ai_for_decision()`；
- `AgentConfig.USE_AGENT`；
- Legacy JSON parser；
- 上层根据 JSON 再执行交易的分支。

这里不做旧 JSON 接口兼容。我们兼容的是现有正常功能和交易行为，不保留已经不需要的双协议设计。

## 6. 工具接口规定

每个工具需要声明自己的名称、参数、返回格式和副作用：

```python
class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec:
        ...

    def invoke(
        self,
        context: ToolContext,
        arguments: Mapping[str, JsonValue],
    ) -> ToolResult:
        ...
```

`ToolSpec` 至少包括：

```python
ToolSpec(
    name="example.sentiment",
    description="Read market sentiment",
    input_schema={...},
    output_schema={...},
    side_effect=SideEffect.EXTERNAL_READ,
    timeout_seconds=20,
    cacheable=True,
    required_capabilities=("network.read",),
)
```

工具副作用分为：

| 类型 | 示例 |
| --- | --- |
| `read_only` | 查询账户和缓存数据 |
| `external_read` | 请求行情、搜索或公共 API |
| `memory_write` | 写入 Agent 记忆 |
| `sandbox_write` | 在 Docker 沙箱中写文件或执行代码 |
| `trading_write` | 执行交易 |

所有工具调用统一经过 `ToolInvoker`：

```text
查找工具
  -> 检查账户是否启用
  -> 检查 capability
  -> 校验输入参数
  -> 执行 timeout
  -> 调用工具
  -> 校验输出
  -> 敏感信息脱敏
  -> 写入 Trace
```

`ToolInvoker.call()` 会同步等待工具返回。系统不提供 async Tool adapter，也不会自动 await 第三方工具返回的 coroutine。

第三方工具必须使用自己的命名空间，例如：

```text
example.sentiment
example.risk_report
```

`core.*` 留给系统内置工具。

### 内置工具如何迁移

当前 `env_wrapper.register_default_tools()` 会拆成几个工具包：

```text
core.market-tools
core.account-tools
core.search-tools
core.sandbox-tools
core.memory-tools
core.trading-tools
```

工具实现本身不改变，只是改成通过统一 ToolProvider 注册，不再把大量工具集中写在一个函数中。

## 7. Prompt 接口规定

Prompt 不再主要以 Python 字符串常量保存，而是普通文本文件加索引：

```yaml
prompts:
  - id: core.react.system
    version: 1.0.0
    file: react/system.txt
    required_variables:
      - portfolio
      - prices
      - current_time
    optional_variables:
      memory_block: ""
```

Prompt Registry 负责：

- 读取 Prompt 文件；
- 检查必需变量；
- 拒绝未知变量；
- 渲染最终内容；
- 记录 Prompt ID、版本和内容 hash；
- 处理内置、外部扩展和账户配置的优先级。

Prompt 覆盖以完整 Prompt ID 或 profile 为单位，不做字符串位置 patch。例如外部扩展可以完整替换 `core.react.system`，但不能声明“在第 300 个字符后插入一句话”。

当前 Python Prompt 会先逐字迁移到文本文件，并通过 golden test 对比，确保空行、变量和最终渲染结果不变。Prompt 文案优化不放在本轮结构重构中。

## 8. 交易接口规定

不管交易来自 Agent、HTTP 还是 WebSocket，最终都通过同一个入口：

```python
class TradeCommandGateway(Protocol):
    def execute(
        self,
        command: TradeCommand,
    ) -> TradeCommandResult:
        ...
```

调用关系是：

```text
core.execute_trade ─┐
HTTP 下单接口      ├─> TradeCommandGateway
WebSocket 下单     ┘       -> 现有 order_matching
                           -> 现有 leverage executor
```

Gateway 不会重新实现撮合逻辑。它负责统一：

- symbol 和 market 校验；
- 市场状态与价格检查；
- operation、direction、sizing 和 leverage 校验；
- 事务提交和回滚；
- 错误码和返回结果；
- 幂等控制。

Agent 工具调用的幂等键使用：

```text
{decision_round_id}:{tool_call_id}
```

模型或网络重复发起同一个调用时，不能产生第二笔订单或成交。

## 8.1 系统如何并发运行多个 Agent

并发发生在账户之间，不发生在系统管理的单个 Agent 步骤之间：

```text
自动交易调度
  -> ThreadPoolExecutor
     -> 账户 A worker -> 同步 Agent.run()
     -> 账户 B worker -> 同步 Agent.run()
     -> 账户 C worker -> 同步 Agent.run()

账户 A worker 内：
  LLM 调用
    -> 同步工具调用
       -> 如需交易，同步 TradeCommandGateway
    -> 下一次 LLM 调用
    -> 返回 AgentRunResult
```

具体规定：

- 保留当前按账户提交到 `ThreadPoolExecutor` 的模式；
- `AGENT_MAX_CONCURRENCY` 继续控制同时运行的账户数量；
- 每个 worker 使用独立数据库 Session/UoW，并在 `finally` 中关闭；
- Agent、Tool、Provider 和 Trade Gateway 对系统都暴露同步接口；
- 系统不使用 asyncio task 调度 Agent，也不管理 Agent 内部 event loop。

如果外部开发者希望在自己的 Agent 内并发调用多个模型或子任务，可以自行在线程池 worker 内实现：

```python
class ExternalAgent:
    def run(self, context: DecisionContext) -> AgentRunResult:
        # 可以在这里自行使用 asyncio 或自己的线程池
        # 但最终必须同步返回 AgentRunResult
        return self._run_with_internal_concurrency(context)
```

这属于扩展内部实现，系统不提供适配和兼容保证。`run()`、`Tool.invoke()` 或 Provider 方法如果直接返回 coroutine/awaitable，系统会将其视为接口错误，而不是自动执行。

## 9. 账户如何选择 Agent、工具和 Prompt

重构后，账户保存一份明确的运行配置：

```json
{
  "agent_id": "core.react",
  "agent_config": {
    "max_steps": 100
  },
  "toolset_ids": [
    "core.default-tools"
  ],
  "disabled_tools": [],
  "prompt_profile_id": "core.react.default",
  "component_versions": {
    "core.react": "1.0.0"
  }
}
```

保存配置时，系统会检查：

- Agent、Toolset 和 Prompt 是否存在；
- 版本是否可用；
- Agent 配置是否符合 JSON Schema；
- 所需 capability 是否已授权；
- Prompt 变量是否完整。

运行时只使用已经通过验证的配置。系统不会因为组件不存在而自动切换到另一个 Agent。

## 10. 如何兼容现有设计

“功能不变”不等于“保留所有旧接口”。本轮兼容分为三类。

### 10.1 保持不变的功能

- ReAct、MultiAgent、AdvancedMultiAgent、RuleAware 都继续存在；
- buy-hold 和 grid baseline 继续独立运行；
- memory、tool routing、rule-aware 等账户能力继续生效；
- Hyperliquid、Alpaca、Pinecone、Chroma、Docker 和 OpenAI-compatible provider 不替换；
- 交易品种、手续费、杠杆、持仓、撮合和市场状态规则不改变；
- 自动交易周期、账户线程池并发限制和非重叠执行不改变；
- 当前页面、账户切换、资产曲线、Trace、评测和合规功能不改变。

### 10.2 通过迁移保持兼容的配置

现有 `agent_type` 会迁移到新的组件 ID：

| 旧值 | 新组件 ID |
| --- | --- |
| `react` | `core.react` |
| `multi_agent` | `core.multi-agent` |
| `advanced_multi_agent` | `core.advanced-multi-agent` |
| `rule_aware` | `core.rule-aware` |

现有字段也会映射到新的运行配置：

- `memory_enabled` -> memory toolset；
- `tool_routing_enabled` -> Agent config/toolset；
- `enable_rule_aware` -> rule-aware Agent 或对应配置；
- 当前 Prompt 组合 -> 对应内置 Prompt profile。

迁移必须幂等，重复启动不会重复创建配置。

### 10.3 明确删除的旧设计

- Legacy JSON 决策接口；
- `AgentConfig.USE_AGENT` 双路径开关；
- `factory.py` 中硬编码 Agent 类型分支；
- `env_wrapper.py` 中集中式工具注册；
- 未注册、前端也不可达的遗留 API，经过确认后删除；
- 迁移完成后的兼容 re-export 和临时 adapter。

这些内容不会长期保留两套实现，否则新架构仍然需要维护旧耦合。

## 11. 内置组件和第三方组件使用同一套机制

内置 Agent、工具和 Prompt 也会被包装成一个内置扩展：

```text
core extension
  agents:
    core.react
    core.multi-agent
    core.advanced-multi-agent
    core.rule-aware

  toolsets:
    core.market-tools
    core.account-tools
    core.search-tools
    core.sandbox-tools
    core.memory-tools
    core.trading-tools

  prompts:
    core.react.*
    core.multi-agent.*
    core.rule-aware.*
```

这样做可以确保公开接口具备真实系统所需的全部能力，而不是只给第三方一套功能较少的接口。

## 12. API 和前端如何支持配置

后端提供组件列表和账户运行配置接口：

```text
GET  /api/extensions
GET  /api/extensions/agents
GET  /api/extensions/toolsets
GET  /api/extensions/prompts
GET  /api/extensions/components/{component_id}/schema

GET  /api/account/{account_id}/runtime-config
POST /api/account/{account_id}/runtime-config/validate
PUT  /api/account/{account_id}/runtime-config
```

这些接口只允许查看和选择已安装扩展，不提供通过网页上传和执行 Python 代码的能力。

前端设置页将增加：

- Agent 选择；
- Agent 参数表单；
- Toolset 选择；
- 工具副作用和权限说明；
- Prompt profile 选择和预览；
- 保存前配置校验。

扩展代码的安装属于部署行为，账户选择扩展属于运行配置行为，两者分开处理。

## 13. 并行开发计划

重构已经拆成 24 个模块任务。任务按依赖关系分批进行。

### 第一批：冻结行为和公共契约

```text
M00 行为基线测试
  -> M01 公共数据契约
```

这一批先固定现有 Agent、交易、HTTP、WebSocket 和 baseline 行为。后续结构变化必须通过这些测试。

### 第二批：可以大规模并行的基础模块

M01 合并后，可以同时进行：

| 任务 | 交付内容 |
| --- | --- |
| M02 | 扩展 Manifest 和静态校验 |
| M03 | Agent Registry 和 Runtime |
| M05 | Tool Registry、Invoker 和权限 |
| M07 | Prompt Registry 和模板校验 |
| M09 | LLM/Memory/Market/Sandbox ports |
| M11 | Trade Command Gateway |
| M18 | App factory 和后台任务生命周期 |
| M19 | Repository 和 Unit of Work |

这些模块依赖公共契约，但彼此之间的文件冲突较少。

### 第三批：迁移现有实现

```text
M04 迁移四种内置 Agent
M06 拆分内置工具
M08 文件化现有 Prompt
M13 实现 Extension Catalog
M20 迁移 Market 和 Cache adapter
```

这一批不修改算法和文案，只给现有实现增加 adapter，然后切换到新注册方式。

四种 Agent、六类工具和多个 Prompt 家族可以进一步分给不同成员并行完成。

### 第四批：核心集成

```text
M10 单一决策编排并删除 Legacy JSON
M12 账户运行配置和数据迁移
M14 扩展管理 API
M16 Trace 与组件版本关联
M21 HTTP/WS 边界整理
M22 Evaluation/Compliance 整理
```

这一批涉及高冲突核心文件，需要按任务指定单一 owner。

### 第五批：用户入口和开源交付

```text
M15 前端扩展设置
M17 SDK、示例和契约测试
M23 前端 API/WS 数据边界
```

最终需要用真实示例验证，用户是否真的可以不修改核心源码完成扩展。

## 14. 如何避免团队开发互相冲突

以下高冲突文件指定单一任务负责人：

| 文件 | 主要任务 |
| --- | --- |
| `trading_commands.py` | M10 |
| `ai_decision_service.py` | M10 |
| `agent/env_wrapper.py` | M06 |
| `agent/tools.py` | M05 |
| `agent/factory.py` | M03/M04，按顺序合并 |
| `main.py`, `startup.py` | M18 |
| `database/models.py` | M19 后 M12 |
| `frontend/app/lib/api.ts` | M23 最终收口 |
| `frontend/app/main.tsx` | M23 |

其他成员可以在新目录中实现接口、adapter、测试和组件，不同时修改这些核心文件。

每个任务 PR 都需要说明：

- 实现了哪个模块任务；
- 修改了哪些公开接口；
- 是否触及交易语义；
- 是否有数据库迁移；
- 运行了哪些测试；
- 是否留下临时 adapter，以及由哪个任务删除。

## 15. 如何确认重构没有改变功能

验证分为四层。

### 行为基线

在移动代码前，记录当前系统的：

- Agent 工具调用顺序；
- Prompt 最终文本；
- 交易执行结果；
- 账户、持仓、订单和成交变化；
- WebSocket 消息；
- baseline 行为。

### 接口契约测试

验证第三方 Agent、Tool 和 Prompt 是否只使用公开接口即可运行。

### 数据状态测试

交易测试必须断言最终数据库状态，不只检查 mock 是否被调用。

### 迁移前后对比

相同输入下对比：

- Prompt 内容和 hash；
- Tool schema；
- Agent 终止原因；
- 订单、成交、持仓和现金；
- Trace 步骤；
- 评测和合规结果。

## 16. 完成后的扩展流程

一个开源用户最终可以按以下步骤新增能力：

```text
1. 复制 examples/extensions 中的示例
2. 填写 alpha-arena-extension.yaml
3. 实现 Agent 或 Tool 接口，或者只添加 Prompt 文件
4. 运行扩展静态校验
5. 运行 Agent/Tool/Prompt 契约测试
6. 将扩展目录加入启动配置
7. 启动系统并在扩展列表中查看状态
8. 在账户设置中选择 Agent、Toolset 和 Prompt profile
9. 保存配置，下一轮决策使用新组件
```

这个流程不要求用户修改：

- `ai_decision_service.py`；
- `trading_commands.py`；
- `env_wrapper.py`；
- Agent factory；
- ORM model；
- 订单撮合和杠杆执行代码。

## 17. 这次重构不做什么

为了控制风险，本轮不包含：

- 修改交易策略；
- 修改 Prompt 文案内容；
- 调整手续费、杠杆或撮合规则；
- 新增交易品种；
- 替换 APScheduler、FastAPI、SQLAlchemy、Redis 或前端框架；
- 设计运行时在线安装 Python 插件；
- 重做前端视觉；
- 修改评测指标定义。

本轮的判断标准很直接：系统能力保持不变，但新增或替换 Agent、工具和 Prompt 时，不再需要修改核心源码。
