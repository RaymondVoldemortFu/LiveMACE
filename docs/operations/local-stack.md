# 本机隔离测试栈

`compose.wave3.yml` 定义单机、单后端 worker 的隔离测试环境，Compose 项目为 `alpha-arena-wave3`。操作该栈应遵守仓库 `AGENTS.md` 中的授权要求。

## 配置与资源

| 项目 | 默认值 |
| --- | --- |
| 前端 | `http://127.0.0.1:15621` |
| 后端 | `http://127.0.0.1:15611` |
| MySQL | `127.0.0.1:13306`，schema `alpha_arena_wave3` |
| Valkey | `127.0.0.1:16379` |
| Compose 配置 | `.wave3/compose.env` |
| 模型与服务配置 | `.wave3/runtime.env` |
| 配置模板 | `deploy/wave3/` |
| 运行证据 | `.wave3/evidence/` |

环境文件应设置权限 `600`。保存独立的数据库密码、`API_KEY_CIPHER_KEY` 和 `DECISION_OPERATOR_TOKEN`；恢复数据库时需要原加密键解密账户凭据。模型、行情与搜索凭据按模板配置。

服务绑定 loopback；后端挂载 Docker socket 管理沙箱。单个进程持有 scheduler、决策锁、Catalog 和沙箱池，部署保持一个 Uvicorn worker。

## 初始化与状态

从模板准备好环境文件后，在仓库根目录执行：

```sh
backend/.venv/bin/python scripts/wave3_runtime.py record-fingerprint
backend/.venv/bin/python scripts/wave3_runtime.py fingerprint

docker compose --env-file .wave3/compose.env -f compose.wave3.yml build
docker compose --env-file .wave3/compose.env -f compose.wave3.yml up -d
backend/.venv/bin/python scripts/wave3_runtime.py schema
backend/.venv/bin/python scripts/wave3_runtime.py seed
backend/.venv/bin/python scripts/wave3_runtime.py status
curl --fail http://127.0.0.1:15611/api/ready
```

`record-fingerprint` 只读被保护的原始数据库，已有记录时拒绝覆盖。`seed` 幂等创建测试账户，初始状态为 MANUAL。账户 ID 以 `status` 输出为准。

`/api/ready` 检查 MySQL、Redis 和 Docker 等依赖；失败返回 503。`/api/health` 展示任务生命周期状态。

## 模型预检与账户调度

```sh
backend/.venv/bin/python scripts/wave3_probe.py model
backend/.venv/bin/python scripts/wave3_probe.py iex
```

模型预检验证文本与工具往返，IEX 预检验证行情及市场时钟。`schedule` 会检查成功预检记录是否匹配待启用账户的模型和 base URL。

在确认实际账户 ID 后，以下示例对账户 1 发起单轮决策、启用后续调度或暂停后续调度：

```sh
backend/.venv/bin/python scripts/wave3_runtime.py round --accounts 1
backend/.venv/bin/python scripts/wave3_runtime.py schedule --accounts 1
backend/.venv/bin/python scripts/wave3_runtime.py pause --accounts 1
```

手动轮次经后端 `/api/agent/round` 与 operator token 派发，并与 scheduler 共享进程内决策锁。账户可保持 MANUAL。暂停影响后续账户选择；在途决策由后端有序排空。

## 日志、停止与备份

```sh
docker compose --env-file .wave3/compose.env -f compose.wave3.yml logs --tail 100 backend
docker compose --env-file .wave3/compose.env -f compose.wave3.yml stop
backend/.venv/bin/python scripts/wave3_runtime.py fingerprint
```

`stop` 保留数据卷。停止后端后，使用 `mysqldump --single-transaction` 备份专用 schema，备份存入权限受限的目录；恢复先在独立 schema 或环境验证。普通重启使用 `up -d`，保留现有数据库和配置。

`.wave3/evidence/source-database.json` 是原始数据库指纹基准，模型预检记录也是运行脚本的输入。维护此目录时保留这些记录与数据库备份。
