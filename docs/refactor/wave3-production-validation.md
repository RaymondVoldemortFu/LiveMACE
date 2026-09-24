# WAVE3 本机生产验证记录

环境：本机 Docker / MySQL，分支 `refactor/main`。验收日期：2026-09-18 至 2026-09-21（Asia/Shanghai）。改动保留在工作区；运行地址 http://127.0.0.1:15621 。恢复、停机与模型设置见 [运行手册](wave3-operations.md)。

WAVE3 核心实现、隔离生产部署、四种内置 Agent 真实交易闭环，以及连续三个完整调度周期均已验收。完整周期要求四账户 `run.result` 且终止原因为 `trade_done` 或 `hold`。当前后端镜像 `sha256:8bce522420c06fa2050fdc0a125ce0c3b4d33ac5437e4d91173e831557b2dd06`。调度间隔已恢复 14400 秒，账户 1–4 已切回 MANUAL，隔离 Compose 实例已停止。本机工作区为测试环境，见仓库根目录 `AGENTS.md`。

## 实现范围

- 独立 `alpha_arena_wave3` MySQL、Valkey、日志卷和模拟账户；生产 DSN preflight 及原库路径保护。原始 SQLite 不挂载到后端或 sandbox。
- 共享冻结 ExtensionRuntime；账户 pinned Agent/Prompt/version、禁用工具、memory/routing 接入真实运行。
- DecisionRoundService 完成账户筛选、同步 AgentRuntime、账户并发、轮次互斥和短事务。Operator API 与 scheduler 使用同一进程和锁。
- Agent、HTTP、WS、baseline、挂单与清算通过统一交易 Gateway 和账户写锁；工具幂等身份由宿主提供，汇总只记录观察结果。
- RuntimeEvent/AgentTrace 保存组件版本、Prompt hash、模型与工具生命周期、成交引用及脱敏后的内容。
- 主 Agent、search、audit 和公开文本工具共享调用预算；网络超时、重试、取消与截止时间有界。
- Alpaca 请求明确使用 IEX，保留源成交/报价时间；默认超过 120 秒、缺失或未来时间戳拒绝交易。缓存读取重新检查源时间，休市数据仅用于展示和估值。
- 前端 Portfolio、行情、模拟订单、Settings、Trace 兼容修复；窄屏布局及服务重启后的连接恢复。

## 当前模型与运行配置

用户指定的 DeepSeek 官方 `deepseek-flash`，地址 `https://api.deepseek.com/v1`。凭据取自本机 `PERSONAL_DEEPSEEK_KEY`，仅存于权限 600 的忽略文件和加密账户字段。

`DEEPSEEK_THINKING_MODE=disabled`；每账户每轮 80 次 HTTP 模型请求、每次最多 4096 输出 token、网络超时 60 秒、最多一次重试、账户截止时间 600 秒。四个模型账户的 Agent 步数上限 12，并发 2。Tavily 提供真实搜索；主模型、辅助模型及 evaluation 均使用同一已验证 provider。模型不支持的图像工具从生产可选目录和直接调用入口排除。

## 自动化与真实服务证据

证据位于本机忽略目录 `.wave3/evidence/`。

| 项目 | 结果 |
| --- | --- |
| 官方模型预检 | `model-probe-final.json`：文本、echo 参数及工具结果回传通过 |
| IEX 开市验证 | `iex-probe-final.json`：AAPL 334.715，源时间 2026-09-18 15:48:15 UTC，市场 OPEN，fresh；真实日 K 线到 9 月 18 日 |
| IEX 时间边界回归 | 真实 SDK 响应解析、两条生产路由、缓存过期、未来/缺失时间及休市拒绝交易通过 |
| 后端回归 | `deepseek-release-full-tests.log`：最终代码 837 passed、2 skipped |
| 专用 MySQL 测试 | `deepseek-release-mysql-tests.log`：51 passed，schema 连续初始化两次；真实行锁、幂等、挂单方向竞争、保证金及强平 |
| 重启持久化 | `deepseek-release-before-restart.json` 与 `deepseek-release-after-restart.json`：6 个账户余额及订单、成交、事件计数完全一致 |
| 前端回归 | 生产构建通过；真实 main.tsx 的 WebSocket 生命周期 6 项测试通过 |
| 原库保护 | 大小、mtime_ns、SHA-256 与执行前指纹一致 |
| 既有工作区保护 | `user-changes-preserved.json`：8 个 M17/SDK 文件与执行前补丁一致 |

## 最终真实 Agent 验收

轮次 `2961224e-db4f-457b-ae71-81d749fcbd4b`，证据 `deepseek-release-round1-results.json`，四账户均有持久化 run.result 和一条 SUMMARY，operator 返回 processed_accounts=4、errors={}。

| Agent | 正常结束原因 | HTTP 模型请求数 |
| --- | --- | --- |
| ReAct | trade_done | 17 |
| Multi-Agent | 返回 hold，但开仓决策未执行；2026-09-21 已在后续轮次补执行并验收 | 37 |
| Advanced Multi-Agent | trade_done | 24 |
| Rule-Aware | trade_done | 12 |

未出现 max_steps、预算耗尽或 run.failed。Advanced 的一次搜索达到自身截止时间后正常完成本轮，保留该降级记录。Rule-Aware 程序化审计正常生成，策略违规分数不等同于程序故障；可选 LLM 审计当前关闭，未登记为真实验证通过。

2026-09-18 该轮将 Multi-Agent 记为待修复。2026-09-21 用稳定调用身份 `multi-agent-final` 把 Manager 最终决策接到 `execute_trade` → Gateway。操作员轮次 `c069322a-8c22-429d-a5bc-eca8b8037d6c` 开仓 SOL 15.5487 @ 115.765，现金 10000→8198.74，receipt `c069322a-...:multi-agent-final` COMPLETED，`termination_reason=trade_done`。生产 Invoker+Gateway 回归覆盖同键重放幂等，以及 `close` + `target_portion_of_balance=0.0` 按 `close_ratio=1` 全平。

调度间隔在验收观察期间为 300 秒，观察结束后恢复 14400 秒，随后停止隔离实例。baseline 周期为 300 秒。

## Multi-Agent 最终决策执行（已通过）

HOLD 不调用 `execute_trade`；open 使用 `target_portion_of_balance`。Manager `close` 在 `size_mode=usd` 时要求正的 `usd_amount`，缺额或无效金额拒绝执行。缺省比例模式下，缺省 `close_ratio` 且目标仓位缺省或为 0 时映射为 `close_ratio=1`；显式非正、非有限或大于 1 的 `close_ratio` 拒绝执行。工具校验失败（含 Invoker `error_code`，例如 `TOOL_INPUT_INVALID`）记 `tool_error`。共享 `execute_trade` 按调用方给出的 sizing 透传；`close` + `size_mode=usd` 且未给 `usd_amount` 时保持 `sizing_mode=usd`、`sizing_value=None`，由 Gateway 拒绝。

调度轮次 `718e4499-904d-410d-8540-5c646a7a292d` 账户 2 曾因 `close` + `target_portion_of_balance=0.0` 触发 `SIZING_VALUE_INVALID`，整轮 `TOOL_INVOKE_FAILED` / `run.failed`。该轮证据保留在 `.wave3/evidence/wave3-three-cycles-before-close-fix.json`。映射修复后，同参数在 Invoker+Gateway 测试中将 SOL 仓位平到 0，receipt 为 `sizing_mode=close_ratio`、`sizing_value="1"`。

## 连续三个完整调度周期（已通过）

2026-09-21 20:27–20:54（Asia/Shanghai）在重建后的后端镜像上观察连续三个完整调度周期，证据 `.wave3/evidence/wave3-three-complete-cycles.json`。完整周期只计 `run.result` 且终止原因为 `trade_done` 或 `hold`。

| 周期 | round | ReAct | Multi-Agent | Advanced | Rule-Aware |
| --- | --- | --- | --- | --- | --- |
| 1 | `b21faae3-f570-4ffc-930b-146a474d4c3c` | hold | hold | trade_done | trade_done |
| 2 | `317f5f15-9993-43b8-afaf-0a05c0675583` | hold | hold | trade_done | hold |
| 3 | `8ec75181-b906-4240-95ff-68e639c8eb0c` | hold | hold | trade_done | trade_done |

三轮均无 `max_steps`、`tool_error` 或 `run.failed`。观察结束时现金：ReAct 10234.55，Multi-Agent 8198.74，Advanced 9127.98，Rule-Aware 1311.85。Multi-Agent 三轮均为策略 HOLD。更早一次观察（证据 `wave3-three-cycles-after-close-fix.json`）第三轮 ReAct 为 `max_steps`，按完成定义中断连续计数，保留为诊断记录。本次观察结束后账户 1–4 切回 MANUAL，调度间隔恢复 14400 秒，随后停止隔离实例。

## 真实轮次驱动的修复

保留失败轮次作为诊断证据；`max_steps`、调用预算耗尽和工具失败不计为完整 Agent 验收。

- `2146597d-40cc-461d-9df2-db0906745116`：Advanced 正常 HOLD、Rule-Aware 正常交易；其他账户暴露步骤/调用预算与输出长度问题。
- `d473d952-997f-42c5-a69f-604be6a2c223`：Rule-Aware 正常交易；ReAct 成交后读取旧持仓、Multi/Advanced 工具契约错误导致本轮未完整通过。
- 修复主流程遗漏的输出上限传递，以及嵌套预算耗尽被误分类为 provider 故障。
- ReAct 账户/历史/文件读取及写入不再使用旧循环的参数缓存；真实 Gateway 回归验证两笔相同参数、不同调用 ID 的成交，以及后续 8 次读取的最新持仓。
- Sandbox 文件读写返回 string、shell 返回 `[exit_code, output]`，修正其错误的 object 输出声明；真实 Invoker 回归覆盖实际返回形状。
- 两种兼容 bridge 保留脱敏后的字段校验信息，模型可根据缺字段提示纠正调用。
- 开仓 sizing 忽略仅用于平仓的 `close_ratio`，避免模型显式传 0 时破坏有效开仓比例。
- 汇总记录使用 `SUMMARY`、Executed=No 和成交后权益，不再伪装成额外 HOLD 成交。
- Rule-Aware 审计使用统一成交后快照，按实际订单核对成交，避免重复计入敞口；规则失败明确报告 ERROR。
- Grid 新卖单扣除现有卖单占用的库存，正常维护取消历史超量卖单，控制挂单数量。
- WebSocket 正常停机关闭码 1000/1001 同样重连，卸载时取消监听和定时器，恢复已选账户。
- Multi-Agent Manager 最终 `open`/`close`/`all_in`/`close_all` 经 `execute_trade` 进入 Gateway，调用身份 `multi-agent-final`，幂等键 `{decision_round_id}:multi-agent-final`。
- Manager `close` 在 `size_mode=usd` 时要求正的 `usd_amount`；缺省比例模式下，缺省 `close_ratio` 且目标仓位缺省或为 0 时按该标的 `close_ratio=1` 全平；显式无效 `close_ratio` 拒绝执行。open 仍忽略仅用于平仓的 `close_ratio`。

## Computer-use 实际操作

使用 Codex In-app Browser 直接操作 Nginx 生产页面，浏览器证据不以 API/日志代替。

| 场景 | 已观察结果 |
| --- | --- |
| Home 曲线 | 1h/1d 切换呈现不同的真实资产曲线，模型标识 deepseek-flash |
| Portfolio 行情 | AAPL/IEX 与 BTC 日 K 线可见，市场 OPEN |
| 模拟订单 | BTC LIMIT 0.001、价格 1，订单 480 从 PENDING 撤为 CANCELLED；零数量被页面拒绝 |
| 账户隔离 | ReAct → Multi-Agent 后余额、持仓、订单对应新账户 |
| Settings | 官方模型与地址回显，密钥显示 Configured；路由开关保存、重开确认；Rule-Aware 标签正确 |
| Agent Status | 实际展开 Runtime events、run.configured；查看组件版本；历史选择从 Live 切到 Historical，切换账户加载对应 Trace |
| Bench Details | baseline 结算及 checkpoint 页面可操作 |
| Compliance/Memory | 最新规则记录可见；未启用 memory 的账户呈现正确空状态 |
| 汇总记录 | 真实新轮次显示 SUMMARY / Executed=No，余额与对应账户一致 |
| 窄屏与桌面 | 574px 和 1440px 实测，侧栏不再遮挡内容，交易按钮可达 |
| 重启恢复 | 最终候选直接停机/启动实测通过：断线显示 Connecting，自动恢复已选 Multi-Agent 账户，余额和订单正确，无需刷新 |

## 独立审查与运行边界

两个子智能体均阅读代码、复现实际调用链缺陷并进行独立复审。审查内容包括交易与清算一致性、模型边界、IEX 源时间、配置保存、sandbox 工具契约、审计、前端恢复，以及 Multi-Agent 最终 close 映射、共享交易工具 sizing、工具故障分类和三周期判定。2026-09-21 复审结论：上述路径以及 Manager 美元平仓缺金额、Invoker `error_code` 分类均已修到根因，无阻断缺陷。未向 GitHub 发布 comment/review。

此实例使用测试资金和项目模拟撮合，真实服务仅提供行情、搜索和模型推理。部署限定本机 loopback、单 backend/scheduler 进程。Docker socket 由后端管理本机 sandbox；此部署不作为公网多租户运行方案。

原始 `alpha_arena_final.sqlite`：大小 `531374080`，mtime_ns `1779099523315738003`，SHA-256 `1aa4409e5b8c2fcd8614e4794ba1a8f0ab991d11ae59e680ebb63e90b38dd8a6`。
