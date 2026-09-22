# WAVE3 完整开发与生产运行计划

状态：核心实现已落地，四种内置 Agent 真实闭环与连续三个完整调度周期已于 2026-09-21 验收。隔离实例已停止；本机为测试环境。当前证据见 [生产验证记录](wave3-production-validation.md)，启动/恢复命令见 [运行手册](wave3-operations.md)。

## 1. 已确定的目标与运行边界

完成原模块计划的 Wave 3 核心集成：G3 trading tool、G6/M10 决策编排、G7 账户配置与 bootstrap Catalog、G8/M14/M21 API/WS、G10/M16 Trace。开发完成后，在当前机器以生产模式运行，并以真实模型、行情和浏览器交互验收。

| 项目 | 确定方案 |
| --- | --- |
| 生产环境 | 当前机器，独立 Docker Compose 项目，MySQL 8.4、Valkey、FastAPI、Nginx/前端产物 |
| 初始数据 | 新建数据库、新测试账户、新采集行情；不导入历史账户或历史交易 |
| 原始数据 | `alpha_arena_final.sqlite` 始终受保护，任何测试、迁移、seed、后台任务都不能写入 |
| 模型 | DeepSeek 官方 API 的 `deepseek-flash`，凭据来自本机 `PERSONAL_DEEPSEEK_KEY` |
| 美股行情 | REST 与存在的流式行情入口统一显式指定 IEX |
| 前端验收 | 必须通过 computer-use 在真实浏览器中点击、输入、切换、刷新并检查画面 |
| 交易性质 | 使用项目现有模拟撮合与测试资金，行情和 LLM 调用使用真实服务 |
| 审查方式 | 实现后独立子智能体阅读代码、验证调用链，修复 findings 后复审 |

原计划中的 M15 完整扩展设置 UI、M23 前端整体数据层重构、M22 评测重构仍归 Wave 4。WAVE3 包含现有页面的联调和必要兼容修复；扩展 API 的新能力先通过 API 验收，不能把尚不存在的 UI 功能登记为浏览器验收通过。

## 2. 开发前基线（历史记录）

| 模块 | 代码证据 | WAVE3 待完成内容 |
| --- | --- | --- |
| M11 / trading | 已有 `SynchronousTradeCommandGateway`、事务和幂等测试；HTTP/WS 已接入 | 将内置交易工具与同一 runtime、round/tool-call 标识完整接线，验证没有旁路重复执行 |
| M10 | `application/decisions/service.py` 仍转调旧入口，返回空 round id 和 0 个账户；请求筛选/并发参数未生效 | 真正实现服务编排、账户配置解析、AgentRuntime 调用、结果汇总和 worker UoW |
| M12/M13/bootstrap | 配置表、校验和扩展 loader 已有；`bootstrap/tasks.py::_load_extension_catalog` 仍是 no-op | 启动时加载并持有 runtime，API 与决策服务消费同一套已冻结 registry |
| M14/M21 | PR #66 已合并；本地已修复令牌、写开关、环境配置、杠杆和错误码 5 个问题 | 纳入基线；补齐剩余 DTO、错误兼容、snapshot/service 边界与生产配置接线 |
| M16 | 有 `AgentRuntimeEvent`/`EventSink`；兼容 factory 仍使用 `NullEventSink` | 持久化事件、组件版本、Prompt hash、tool/trade 引用以及脱敏 |
| IEX | `AgentConfig.ALPACA_USE_IEX_FEED` 默认 true，但 `market_data_config.py` 仍写死 `ALPACA_US_FEED="sip"` | 合并两个 feed 配置入口，覆盖所有请求构造和缓存来源 |
| 部署 | Docker Desktop 27.5.1 / Compose 2.32.4 已就绪，架构 aarch64；本机 MySQL 3306 和现有 Redis 6380 在运行 | 使用独立服务名、端口、卷和环境文件，验证 ARM64 容器及 sandbox 构建 |
| 前端 | 现有账户设置、交易、资产曲线、Trace、Memory、Compliance 页面；computer-use 可用 | 对生产构建产物进行可见交互验收及缺陷复测 |

此前相关回归为 312 项通过、1 项跳过，独立审查验证 77 项通过；这是当前基线记录，不能代替 WAVE3 完成后的验收。

## 3. 原始数据库保护与隔离部署

这部分必须在启动任何新应用进程或迁移之前完成。

1. 记录原始 SQLite 的规范路径、文件大小、mtime 和 SHA-256；检查是否存在 WAL/SHM 和仍在使用它的进程。遇到外部写入活动，先定位所有者，不能把变化归因于本次运行或删除 journal 文件。
2. 生产配置只允许目标 MySQL schema `alpha_arena_wave3`。启动、迁移、seed、维护脚本共享 preflight；缺失 DSN、指向默认旧库、指向受保护 SQLite 或发生隐式 SQLite fallback 时，在数据库初始化前退出。
3. 原始文件及其目录不挂载到 backend 或 Agent sandbox。测试容器仅挂载必要代码、受控扩展目录及独立运行产物目录，避免把整个仓库作为可写运行数据目录。
4. 新建专用 MySQL 用户，仅授予本次 schema 所需权限。数据库创建/迁移与普通运行权限明确区分；不得将现有 `alpha_arena` 作为临时目标。
5. 使用新的 Compose 文件 `compose.wave3.yml`，项目名 `alpha-arena-wave3`，独立 MySQL/Valkey 卷与 sandbox 标签。不得复用现有 Compose 的固定 `container_name` 和 `mysql-data` 卷。
6. 默认仅绑定本机：前端 `127.0.0.1:15621`、后端 `127.0.0.1:15611`。MySQL/Valkey 保持容器网络内访问；确需宿主调试时使用 `13306/16379`，预检端口占用。
7. 新环境文件单独保存并忽略版本控制；配置专用 `DATABASE_URL`、Redis 地址/前缀、账户密钥加密键和模型参数。验收产物中只保存脱敏配置。
8. 每个集成测试使用独立测试 schema；生产运行 schema 与测试 schema 分开。运行结束再次核对原始文件指纹；只读校验不应打开可写 SQLite connection。

如果 MySQL 暂不可用，优先修复本机 Docker/MySQL 条件；开发阶段可以使用新建的临时 SQLite 文件，但生产验收必须以 MySQL 完成。

## 4. 开发任务与完成门槛

### W3-00：基线、构建与运行前检查

- 整理本地 5 项修复为明确改动集；保留已有 M17/SDK 工作区修改的来源和边界，不混入无关变更。
- 运行相关 characterization/contract tests，记录当前 OpenAPI/WS 契约和现有前端功能。
- 实现上一节的数据库 preflight、独立 Compose、环境示例及运行清单。
- 验证 backend 的 Python/uv lock、ARM64 依赖、构建上下文与 package resources，frontend 的 pnpm lock 和生产构建；`build:backend` 的占位输出不作为构建成功证据。
- 检查 Docker sandbox 在新项目下的容器发现、清理和卷路径，避免清理其他运行实例的 sandbox。

完成门槛：原始 SQLite 保护测试通过；空 MySQL 可初始化；容器可构建；启动检查能拒绝错误数据库目标。

### W3-01：IEX 全路径接线

主要文件：`config/market_data_config.py`、`config/agent_config.py`、`services/alpaca_market_data.py`、market adapter/cache 及对应测试。

- 建立统一的 feed 配置解析，生产配置显式为 `iex`；兼容旧配置入口时也映射到相同有效值，消除 helper 之间的分歧。
- 覆盖 latest trade、quote、snapshot、历史 K 线、延迟回看、批量查询、后台刷新，以及仓库中存在的流式订阅入口。
- 校验美股价格、估值和订单前置检查读取同一行情来源；在数据元信息/运行记录中保存来源与时间。
- 缓存按 provider/feed 隔离，或在本次新运行中启用独立 cache namespace，防止混入其他实例的 SIP 数据。
- 403、空行情、过期价格、休市必须返回明确状态，不能把 0 当有效价格继续下单；不自动回退 SIP。
- 使用支持列表中的美股标的验证 IEX 历史 K 线和最新数据；实时新鲜度验收在美股交易时段进行。

完成门槛：所有 Alpaca 行情请求构造测试均显式 IEX；真实账户可读取 IEX 数据；前端休市/无数据状态合理。

### W3-02：共享 ExtensionRuntime 与账户运行配置（G7）

主要文件：`benchmark/bootstrap/*`、`benchmark/extensions/*`、`benchmark/accounts/*`、`services/extension_config_service.py`。

- 用真实扩展加载替换 bootstrap no-op，按环境加载、校验、冻结并持有 `ExtensionRuntime`。
- 让 API、Agent selection、Tool runtime、Prompt renderer 消费同一个 runtime 实例；避免 API 显示的组件与实际执行 registry 不一致。
- 新旧账户配置优先级明确：已有 runtime config 使用其 pinned version；没有记录时按已有映射迁移；配置无效时标记并停止该账户本轮运行。
- 禁用/缺失组件、版本不可用、能力不足不能默默切换 Agent；API 给出可操作的错误。
- 接通显式 Prompt profile、工具禁用和可用 toolset 选择。当前 `list_toolsets()` 是空实现，需结合 registry 实际能力完成或明确受支持配置；不能对任意未知 toolset 仅警告后当作已生效。
- 保持现有 SettingsDialog 的旧字段与有效 runtime config 的一致性，防止界面保存成功但下一轮继续执行旧配置。
- 保留 UTC 乐观锁和账户更新开关，验证两客户端竞争保存、关闭开关、服务重启后的行为。

完成门槛：通过 API 保存后，下一轮 Trace 显示真实选中的 agent/profile/version；禁用工具确实不可调用；失效账户不会执行交易。

### W3-03：内置交易工具接入 M11（G3）

主要文件：`benchmark/builtin/tools/*`、tool context/invoker、`services/agent/trade_execution_tool.py` 的受控适配入口。

- 完成 `core.execute_trade` 的注册与注入，使 Agent 工具、HTTP、WS 共用应用交易边界。
- 从真实 ToolContext 提供 account id、round id、tool-call id；账户身份由宿主确定，幂等键稳定贯穿重复调用和重试。
- 禁止将 ORM 实体或 worker Session 传给扩展；每条交易命令由 Gateway 管理独立写事务。
- 保留现有 Decimal、现金/保证金、持仓、撮合、佣金和拒绝语义；手动 CRYPTO 杠杆 1～50、Agent 策略上限 10、美股 1 倍。
- 同一 tool-call 重放、并发重复、提交前异常、持久化后客户端重试分别验证。

完成门槛：同一工具交易最多产生一份财务副作用；失败事务完整回滚；决策汇总层不再次下单。

### W3-04：真正落地 DecisionRoundService（G6/M10）

主要文件：`benchmark/application/decisions/*`、`services/trading_commands.py`、`services/ai_decision_service.py`、`services/auto_trader.py`。

- `RunDecisionRound.account_ids/max_concurrency/trigger` 实际影响执行；生成真实 round id，返回准确 processed count 和逐账户错误。
- 将账户选择、行情准备、配置解析、上下文构造、并发提交、完成处理和持久化拆为可验证协作者。
- 保留 `ThreadPoolExecutor + as_completed()`；不同账户可并行，单账户内部 Agent/LLM/Tool 调用同步，跨轮锁保持非重叠语义。
- worker 使用独立短生命周期读 UoW，构建不可变快照后关闭；长 LLM 等待不占用长期事务。工具需要新鲜状态时另开查询 UoW，交易另开写 UoW。
- 生产调用切换为 `AgentRuntime.run()` 和 `AgentRunResult`；清理只为旧调度保留的分发/转换链。已经删除的 Legacy JSON 路径只做回归检查。
- HOLD、失败、取消、超时和已执行交易引用分别处理；记录失败不能掩盖原异常或变成补偿性下单。
- buy-hold/grid 保持独立 baseline 入口，并确认调度仍实际运行二者。
- 停机先停止新轮次，取消未开始任务，等待在途同步调用按超时退出并释放 UoW、缓存锁与 sandbox。

完成门槛：四种内置 Agent、两个 baseline、外部最小 Agent 可运行；账户筛选、并发限制、无重叠、关闭资源、交易去重均有行为测试。

### W3-05：Trace 与可追溯性（G10/M16）

- 复用现有 EventSink/AgentRuntimeEvent；先核对公共规范，再补充所需事件字段或适配，避免建立另一套平行事件接口。
- 覆盖 run started/finished/failed、prompt rendered、LLM call、tool start/finish、trade result。
- 保存 event id、account/round/trace id、agent/tool/prompt id 和 version、Prompt hash、termination reason、trade ref。
- 使用兼容现有 AgentTrace 的 repository/service 与版本化 DTO；SQLite/MySQL migration 幂等，旧 Trace 保持可读。
- 在落库及日志输出前脱敏嵌套 secret、Authorization 和 URL credentials；保留 LONGTEXT 能力，限制大输出摘要。
- 不持有跨整个模型调用的写事务；Trace 写入失败不能覆盖 Agent 原始错误，也不能触发交易重放。

完成门槛：API 和现有前端 Trace 页面能关联同一轮组件、工具与成交；脱敏、旧数据和写入失败测试通过。

### W3-06：API/WS 收尾与前端兼容（G8/M14/M21）

- 基于实际注册路由清单补齐 request/response DTO、错误码和序列化；服务返回稳定的数据结构，避免 ORM 跨 session 边界泄漏。
- 新标准错误信封与现有前端 `detail` 解析做兼容测试；保留状态码语义，参数错误与基础设施错误分开。
- 将 WS snapshot 查询与发送解耦，保持 fast/full payload、资产曲线周期及账户维度不变。
- 验证 subscribe/switch/disconnect/reconnect 的 connection 与 snapshot job 清理；避免 user id 与 account id 混用。
- 下单完成后读取真实已提交状态，核对独立写事务后的会话刷新、余额、订单与成交展示。
- readiness 以实际运行依赖为准。代码当前为 `/api/ready`，部署和文档统一该路径；未就绪返回可供健康检查识别的失败状态。
- 只修复 WAVE3 接口接线所需的现有前端功能，保留视觉和交易展示语义。

完成门槛：API/WS 契约回归、前端 build 通过，且下节 computer-use 验收全部有明确结果。

## 5. 模型预检与低成本运行策略

当前模型按用户指定采用 DeepSeek 官方 `deepseek-flash`，已通过真实文本与 echo 工具调用往返。配置统一覆盖主 Agent、搜索辅助、规则审计及 evaluation，base URL 为 `https://api.deepseek.com/v1`。

- `DEEPSEEK_THINKING_MODE=disabled`；单次输出上限 4096 token、网络超时 60 秒、最多一次重试。
- 每账户每轮最多 80 次 HTTP 模型调用，包含嵌套辅助调用；账户截止时间 600 秒。测试账户 Agent 步数上限 12，并发 2。
- 先单账户，再四种 Agent 并发运行。达到预算或 max_steps 不计为正常完成，必须检查持久化 `run.result` 的终止原因。
- `SEARCH_PROVIDER=tavily` 使用独立搜索凭据；不适用于当前 provider 的图像工具从生产工具目录排除。
- 调度启用脚本要求 probe 的模型、base URL 与当前运行配置及待启用账户一致。
- 真实 usage 用于记录调用量，不以其他 provider 的价格推算 DeepSeek 账单。

## 6. 自动化测试与真实运行矩阵

| 层级 | 环境 | 必须证明的行为 |
| --- | --- | --- |
| 单元/契约 | fake provider、临时 SQLite | selection、配置校验、tool capability、同步 SPI、错误映射、事件脱敏 |
| 数据库集成 | 独立 MySQL 测试 schema | migration 重复执行、乐观锁、幂等 receipt、行锁顺序、Decimal、LONGTEXT、回滚与重启 |
| 调度集成 | 本地隔离 runtime | 多账户并行、单账户串行、轮次不重叠、筛选生效、停机清理 |
| Provider 集成 | DeepSeek 官方 API + IEX + 必需的行情源 | 文本/工具往返、限流/超时、IEX 历史与最新行情、数据新鲜度 |
| API/WS | 新运行数据库 | 现有 wire 契约、配置生效、无效配置、错误码、重连、订单及资产一致 |
| 浏览器验收 | Nginx 生产产物 + computer-use | 页面真实可操作，显示与服务端事实一致 |
| 生产观察 | Docker FULL 模式 | 后台实际完成轮次、资源释放、服务重启、数据持久化 |

真实 provider 测试显式标记 integration。数据库并发、幂等和迁移不能仅以 SQLite 通过代替 MySQL 结果。缺少外部服务的关键验收项记为待完成，不能以 skip 计为通过。

## 7. computer-use 前端验收清单

通过 `cua_repl` 打开 `http://127.0.0.1:15621`，对实际生产构建页面操作。每一步记录动作、预期、画面结果和相应 account/trace/order 标识，必要时用截图留证；日志和数据库断言作为补充。

| 场景 | 直接操作 | 可见通过标准 |
| --- | --- | --- |
| 首次访问与刷新 | 打开页面、刷新、从深链接重新进入 | 页面完整呈现；新账户和空数据状态明确，无白屏或无限加载 |
| 账户切换 | 在两个测试账户间来回切换 | 名称、余额、持仓、订单和 Trace 对应同一账户，无串号 |
| 现有设置 | 修改 Agent 类型、模型、memory/routing 等已有设置，保存后重开 | 值正确回显，失败提示可读；下一轮实际配置与保存一致 |
| 行情 | 切换 crypto/美股、选择支持的标的、查看 K 线 | IEX 数据可显示；空数据/休市可区分；时间和来源可核对 |
| 资产曲线 | 切换 5m/1h/1d，刷新和重新选择账户 | 曲线与表格对应周期和账户；新运行数据不足时正确提示 |
| 模拟订单 | 用测试账户创建可控限价单、查看订单、撤单，再测试非法价格/数量 | 状态和资金按后端结果更新；错误留在页面可见位置 |
| 决策与 Trace | 一轮完成后打开对应记录、查看工具步骤和版本信息 | account/round/trace/trade 关联正确，长内容可读 |
| WS 恢复 | 断开并恢复本次测试 backend，观察原页面 | 重连后状态恢复，不重复注册、不重复下单、不串账户 |
| 评测与记忆 | 打开 Compliance/Memory 页面、切换账户 | 空状态和新记录正确；不操作非本次运行的数据 |
| 布局 | 调整浏览器窗口宽度、滚动对话框和长表格 | 关键按钮可点击，错误信息与表格不被遮挡 |

记录格式：用例编号、时间、URL、动作、预期、实际结果、截图/状态证据路径、相关服务端 id、问题及复测结果。修复前端问题后必须重新走实际操作步骤；只看日志消失不算通过。

## 8. 开发完成后的生产启动与观察

1. 固定本次源代码版本与镜像标识，记录未提交改动的归属；生成原始 SQLite 指纹。
2. 确认 Docker daemon 就绪，执行独立 Compose 配置预检，先启动本项目专用 MySQL/Valkey。
3. 对 `alpha_arena_wave3` 执行 schema/migration；生成新测试账户。四种内置 Agent 与 buy-hold/grid 分批启用，其余账户保持不运行。
4. 用 schema-only / no-background 模式验证配置、数据库和查询路径。完成模型、IEX 及 sandbox 预检后再切换 FULL。
5. 首次 FULL 运行保持单 backend/scheduler 实例，避免多 Web worker 重复启动调度。生产前端使用 Nginx 静态构建，backend 不使用 reload。
6. 设置带时区、明确的首次执行时间；先手动触发指定账户的一轮，再用 300 秒的本次验收调度间隔观察至少 3 轮及一次优雅重启。控制调用预算，轮次超时不能与下一轮重叠。
7. 验证订单/成交/持仓/余额与 Trace，完成 computer-use 清单，检查 MySQL 数据和缓存命名空间只属于本次运行。
8. 在美股开市期间核对 IEX 源成交时间；默认超过 120 秒、缺失或未来时间戳不可用于交易。保留历史数据和休市估值路径。
9. 观察稳定后，将调度间隔恢复为本项目常规 14400 秒，报告下次运行时间、已启用账户、模型和服务 URL，保留独立生产实例运行。
10. 若出现重复成交、跨账户数据、错误 DB 目标、关键依赖不可用或持续失控重试，先停止本次实例的新轮次并排空在途工作，再修复和复测。回滚使用上一个镜像和本次新库快照；不会把原始 SQLite 当作回滚写入目标，也不删除数据卷。

## 9. 顺序、并行边界与审查迭代

关键路径：`W3-00 → W3-02 → W3-03/W3-04 → W3-05/W3-06 → 生产运行与 browser 验收`。

W3-01 的 IEX 修复可与 W3-02 并行；Trace 契约可先设计，持久化接线在 M10 返回结果稳定后集成。若使用多个开发任务，`trading_commands.py/ai_decision_service.py`、`database/models.py`、`bootstrap/*`、`api/ws.py` 分别指定单一集成负责人。

| 里程碑 | 交付物 | 合并/推进门槛 |
| --- | --- | --- |
| A：隔离基础 | Compose、preflight、新库与 IEX 配置 | 原库保护及容器构建通过 |
| B：运行配置 | bootstrap runtime、配置/工具/Prompt 生效 | API 保存到实际运行闭环通过 |
| C：核心编排 | M10、交易工具、UoW/幂等 | MySQL 财务与并发验证通过 |
| D：可观测与接口 | M16、M14/M21 收尾 | Trace 与 HTTP/WS 契约通过 |
| E：运行验收 | 真实模型/行情、browser 记录、运行报告 | computer-use 和生产轮次观察通过 |

按里程碑提交可独立审查的改动，不把整个 WAVE3 压成单次大提交。每个高风险里程碑和最终候选版本都执行“独立代码审查 → 本地复现 → 修复 → 针对性测试 → 独立复审”。发现 P1/P2 必须解决或明确阻塞原因；不能只用测试全绿关闭 finding。审查结果先在对话中报告，沿用本任务不擅自发布 GitHub comment 的约定。

## 10. 最终完成定义与交付

- [x] 连续三个完整生产调度周期：`b21faae3-...`、`317f5f15-...`、`8ec75181-...`，证据 `wave3-three-complete-cycles.json`。

- [x] WAVE3 的生产入口实际使用共享 runtime、账户配置、同步 AgentRuntime、ToolInvoker 和 Trade Gateway。
- [x] 四种内置 Agent 完整真实闭环：ReAct / Advanced / Rule-Aware 调度成交；Multi-Agent 操作员轮次 `c069322a-...` 经 Gateway 开仓 SOL。
- [x] MySQL 下并发、幂等、回滚、migration、Trace 持久化和配置冲突通过。
- [x] 主模型为已验证的 DeepSeek 官方 `deepseek-flash`；调用数和成本可追踪。
- [x] IEX 全路径验证完成，实时新鲜度已在适当交易时段验收。
- [x] 全套相关后端测试、lint、前端生产构建和真实 computer-use 用例通过。
- [x] 独立审查无阻断缺陷：close 映射、Invoker 工具错误分类与三周期判定的复审无 P1/P2。
- [x] 当前机器的隔离实例已启动、观察、重启验证后停止；启动/恢复步骤见运行手册。本机工作区为测试环境。
- [x] 原始 `alpha_arena_final.sqlite` 指纹未改变；新数据只写入本次数据库/卷。

交付物包括：代码与测试、`compose.wave3.yml`、脱敏环境示例、preflight/启动与停止说明、migration 说明、模型与 IEX 验证记录、computer-use 验收证据、独立审查结论，以及 `wave3-production-validation.md` 运行报告。报告必须区分已通过、失败和待验收项目。

模块依据：[原开发波次](modules/module-groups.md)、[公共接口规范](modules/000-public-interface-spec.md)、[M10](modules/M10-decision-orchestration.md)、[M14](modules/M14-extension-management-api.md)、[M16](modules/M16-trace-observability.md)、[M21](modules/M21-http-websocket-boundaries.md)。
