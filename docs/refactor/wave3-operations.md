# WAVE3 本机生产运行手册

适用范围：单台机器、单后端 worker、Docker Compose 项目 `alpha-arena-wave3`。交易使用新测试账户的模拟资金。当前验收结论见 [生产验证记录](wave3-production-validation.md)。

## 环境与持久化

| 服务 | 本机地址 / 资源 |
| --- | --- |
| 前端 Nginx | http://127.0.0.1:15621 |
| FastAPI | http://127.0.0.1:15611 |
| MySQL 8.4 | 127.0.0.1:13306，schema `alpha_arena_wave3`，用户 `wave3` |
| Valkey | 127.0.0.1:16379，cache prefix `wave3:iex` |
| 数据卷 | `alpha-arena-wave3_wave3-mysql`、`alpha-arena-wave3_wave3-logs` |
| Sandbox | 标签 `open-alpha-arena-bench.instance=alpha-arena-wave3` |
| 运行配置 | `.wave3/compose.env`、`.wave3/runtime.env`，权限 600，已忽略版本控制 |

后端仅挂载专用日志卷和 Docker socket。原始 `alpha_arena_final.sqlite` 及所在目录不挂载到容器。生产 DSN 必须明确指向 `alpha_arena_wave3`；数据库保护检查覆盖应用引擎、schema bootstrap 和 startup migration，拒绝受保护路径、别名、硬链接及 SQLite file URI。

当前部署仅绑定 loopback；Docker socket 给予后端管理本机容器的能力。本手册适用于用户已授权的本机运行范围，尚不提供公网多租户隔离。单进程同时持有 scheduler、决策锁、扩展 registry 和 sandbox 池，不能直接增加 Uvicorn workers 或启动第二个相同 instance 的决策进程。

## 首次配置

当前机器已经配置并启动，可跳到运行与观察。新部署按以下步骤准备：

1. 从 `deploy/wave3/compose.env.example` 和 `runtime.env.example` 复制到 `.wave3/` 的同名环境文件，设置权限 600。MySQL 密码使用独立 URL-safe 随机值。
2. 将本机 `PERSONAL_DEEPSEEK_KEY` 的值写入独立环境文件的主模型、audit、evaluation 三组 API key。三组模型均为 `deepseek-flash`，base URL 为 `https://api.deepseek.com/v1`。配置 `DEEPSEEK_THINKING_MODE=disabled`、Alpaca 凭据和 Tavily 搜索凭据。
3. 用 `cryptography.fernet.Fernet.generate_key()` 生成并保存 `API_KEY_CIPHER_KEY`，另生成随机 `DECISION_OPERATOR_TOKEN`。保留加密键与数据库备份，否则无法解密已有账户凭据。
4. 设置带时区的 `AI_TRADE_FIRST_EXECUTION_TIME`。常规 AI 周期 14400 秒，baseline 周期 300 秒。
5. 首次启动前记录原库指纹。该操作只读原文件，已有基准文件时拒绝覆盖：

```sh
backend/.venv/bin/python scripts/wave3_runtime.py record-fingerprint
backend/.venv/bin/python scripts/wave3_runtime.py fingerprint
```

仓库已有 Python venv 可执行脚本；新机器先按 backend 锁文件安装依赖。容器构建自行安装锁定依赖。

## 启动、停止与检查

在仓库根目录运行：

```sh
docker compose --env-file .wave3/compose.env -f compose.wave3.yml build
docker compose --env-file .wave3/compose.env -f compose.wave3.yml up -d
backend/.venv/bin/python scripts/wave3_runtime.py schema
backend/.venv/bin/python scripts/wave3_runtime.py seed
backend/.venv/bin/python scripts/wave3_runtime.py status
curl --fail http://127.0.0.1:15611/api/ready
```

`seed` 幂等创建六个独立账户，初始模拟资金各 10000，初始状态 MANUAL。当前机器的 ID 1–4 分别为 ReAct / Multi-Agent / Advanced Multi-Agent / Rule-Aware，ID 5–6 为 buy-hold / grid。使用 `status` 核对实际 ID 后可启用 baseline：

```sh
backend/.venv/bin/python scripts/wave3_runtime.py schedule --accounts 5 6
```

手动面板提供多头买入/卖出及 1–50 倍多头杠杆；已有空头通过 Agent 的带方向交易入口管理。手动请求会拒绝修改已有空头，以及向现有仓位增加不同杠杆的数量。手动、baseline、Agent 和挂单调度均通过 Gateway 的账户写锁串行结算。杠杆挂单复用原始订单，结算保证金、损益与利息；暂时无法成交的订单保留 PENDING，并单独回滚结算。

保证金监控在同一账户锁内重新读取现金和全部持仓，按现金加持仓权益、减应计利息判断风险，再按当前方向平仓。任一估值行情不可用时终止该账户的清算事务；正常平仓可用释放的保证金结算利息。强平后的负余额保留为模拟账户债务。

当前 Mac 的 Docker Desktop credential helper 曾阻塞匿名拉取，因此本次使用忽略目录中的匿名 Docker 配置。若普通 `docker compose` 在 credential helper 卡住，可使用本次已创建的配置：

```sh
docker --config .wave3/docker --host unix:///Users/fuleiming/.docker/run/docker.sock compose --env-file .wave3/compose.env -f compose.wave3.yml ps
```

其他 compose 操作也可用同一前缀。该路径属于本次机器，新机器用自己的 Docker socket。

`/api/ready` 除启动任务状态外，还实时检查 MySQL `SELECT 1`、Redis ping、Docker ping；任一不可用返回 503。网络阶段有界，Compose 探针允许 25 秒 HTTP / 26 秒总等待。`/api/health` 主要呈现任务生命周期；外部模型余额不属于就绪检查。

```sh
docker compose --env-file .wave3/compose.env -f compose.wave3.yml logs --tail 100 backend
docker compose --env-file .wave3/compose.env -f compose.wave3.yml stop
```

`stop` 保留数据卷，后端最多等待 120 秒退出。重启仍用 `up -d`。暂停账户调度使用 `pause --accounts ...`，只影响下次账户选择，在途决策由后端优雅停机排空。

## 模型和 IEX 验证

```sh
backend/.venv/bin/python scripts/wave3_probe.py model
backend/.venv/bin/python scripts/wave3_probe.py iex
```

模型预检最多三个短请求：文本、无副作用 echo tool、工具结果回传；不连接交易工具，不重试余额不足。结果写入 `.wave3/evidence/model-probe-final.json`。IEX 预检使用 AAPL 最新成交、日 K 线与市场时钟。IEX 始终显式指定，休市不能作为可交易的实时行情。

`deepseek-flash` 已通过官方文本与工具往返预检。IEX 最新成交保留源时间戳；`ALPACA_MAX_QUOTE_AGE_SECONDS` 默认 120，缺失、未来、超龄报价拒绝交易，缓存不能延长该有效期。

先通过模型预检，再执行：

```sh
backend/.venv/bin/python scripts/wave3_runtime.py round --accounts 1
backend/.venv/bin/python scripts/wave3_runtime.py round --accounts 2 3 4 --concurrency 2
```

手动轮次通过本机后端 `/api/agent/round` 及 operator token 派发；账户仍可保持 MANUAL。CLI 不创建宿主 sandbox 池，不直接运行第二套 AgentRuntime。请求与 scheduler 共享进程内锁，重复并发触发返回 409。Nginx 不代理此运维入口。CLI 根据账户数、单账户预算和准备阶段余量设置超时。

通过真实轮次、Trace 和浏览器复测后，可显式启用持续 AI 调度：

```sh
backend/.venv/bin/python scripts/wave3_runtime.py schedule --accounts 1 2 3 4
```

脚本要求成功且模型/base URL 与运行配置和待启用账户一致的预检记录。检查 `errors` 以及 `run.result.termination_reason`，max_steps 不计为正常完成。当前配置每账户每轮最多 80 次 HTTP 模型调用、每次输出 4096 token、网络 60 秒、至多一次重试、账户截止时间 600 秒。四账户总上限 320 次请求，主 Agent、search、audit 与公开文本工具共享预算。实际 usage 持久化到事件；账单以 DeepSeek 为准。

## 备份与恢复

停止本次 backend 后，对专用 schema 做 `mysqldump --single-transaction`，备份文件存于权限受限、忽略版本控制的目录。保留 `.wave3/runtime.env` 中的加密键。只将备份恢复到独立新 schema/环境验证；原始 SQLite 不是回滚目标。

发布前记录镜像 ID，保留上一个镜像。出现重复交易、跨账户数据、持续依赖故障时，先暂停本次账户并停止后端，再恢复已验证的镜像和本次数据库备份。不要用 `down -v` 做普通重启。

每次验收后执行 `fingerprint`，确认原始文件大小、mtime 和 SHA-256 均与基准一致。
