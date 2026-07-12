# M18：App Factory、Bootstrap 与后台任务生命周期

## 交付目标

拆分 `main.py` 的装配、迁移、seed、密钥迁移和 runtime 启动，使扩展 catalog 在 Agent scheduler 前确定性装载，并允许测试启动无后台任务 app。功能与默认启动顺序保持。

## 文件边界

- 主改：`backend/main.py`、`services/startup.py`、`services/scheduler.py`。
- 新增：`backend/alpha_arena/bootstrap/{app,schema,seed,credentials,runtime,tasks}.py`。
- 不修改业务 job 函数和调度周期。

## 接口

```python
def create_app(settings: AppSettings, mode: StartupMode = StartupMode.FULL) -> FastAPI
async def bootstrap_runtime(context: BootstrapContext) -> RuntimeHandle
async def shutdown_runtime(handle: RuntimeHandle) -> None
```

`StartupMode`: `FULL`、`NO_BACKGROUND`、`SCHEMA_ONLY`。生产默认 FULL；测试显式传 mode，不通过隐藏环境判断。

## TODO

- [ ] app factory 只装 middleware/routes/static/lifespan；import 不建表、不连 Redis、不启动线程。
- [ ] schema、seed、placeholder cleanup、API key migration 分为幂等步骤并记录结果。
- [ ] runtime 顺序固定：DB ready -> Redis ready -> extension catalog -> Docker -> scheduler jobs。
- [ ] 后台 task descriptor 声明 id、start、stop、required、dependencies；重复启动不重复注册。
- [ ] 保持当前 AI/baseline first-run timing、market tasks、curve backfill、price cleanup、margin/order/eval job 周期。
- [ ] shutdown 逆序且幂等；部分 start 失败只 stop 已启动资源。
- [ ] `/api/health` 业务保持；新增内部 readiness service 由 M21 暴露。

## 验收

- `import main` 无 Redis/Docker/DB 写副作用。
- NO_BACKGROUND 可执行 route tests 且 scheduler job 数为 0。
- FULL 启动的 job id、interval、first execution 与 M00 fixture 一致。
- 重复 bootstrap/shutdown 无重复 job、悬挂线程或 container lease。

## 前置与并行

前置 M00。可早期独立实施；与 M13 集成 extension catalog 的调用点时只依赖其 facade。

