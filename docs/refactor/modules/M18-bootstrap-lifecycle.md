# M18：App Factory、Bootstrap 与后台任务生命周期

## 交付目标

拆分 `main.py` 的装配、迁移、seed、密钥迁移和 runtime 启动，使扩展 catalog 在 Agent scheduler 前确定性装载，并允许测试启动无后台任务 app。功能与默认启动顺序保持。

## 文件边界

- 主改：`backend/main.py`、`services/startup.py`、`services/scheduler.py`。
- 新增：`backend/benchmark/bootstrap/{app,schema,seed,credentials,runtime,tasks}.py`。
- 不修改业务 job 函数和调度周期。

## 接口

```python
def create_app(settings: AppSettings, mode: StartupMode = StartupMode.FULL) -> FastAPI
async def bootstrap_runtime(context: BootstrapContext) -> RuntimeHandle
async def shutdown_runtime(handle: RuntimeHandle) -> None
```

这里的 async 只用于 FastAPI lifespan/服务启停，不是 Agent SPI，也不参与账户 Agent 调度。后台自动交易仍调用同步 `DecisionRoundService`，由其内部 `ThreadPoolExecutor` 并行账户 worker。

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

## 实现约束：Scheduler Runtime

- APScheduler 仅作为执行引擎；应用以不可变 JobSpec 和 job family 作为调度事实来源。
- 每个 job registration 同时记录唯一 owner（standalone 或一个 family）、lifetime（one-shot/recurring）和执行终态；所有安装、删除与 reconcile 必须经过同一所有权检查。
- one-shot occurrence 一经 callback admission 即写入独立的持久化 consumed ledger；相同 job id/run_date 的 reconcile 是跨 scheduler generation、跨进程重启的幂等声明，失败恢复不得重新执行已消费 occurrence，新 run_date 才是新任务。活跃 registration 仍按 generation 重建，不得把 APScheduler job store 误作消费事实源。
- start、shutdown、job reconcile、remove 与 callback admission 使用同一个 RLock/Condition 状态机；不得再引入并列 token/registry 锁。
- 逻辑任务组（特别是 AI/baseline first-run 与 recurring jobs）必须通过一次 reconcile 更新；失败恢复完整旧组，禁止混合配置继续运行。
- 每次 generation 创建独立 cancellation event；旧 callback 永远不能观察到被清除的旧 event。
- shutdown 必须先阻止新 callback，再等待已进入 job body 的调用。超时或 stop callback 失败必须传播至 lifespan 调用方，不能只记日志。
- Runtime registry 必须区分无资源的 START_FAILED、持有待清理资源的 START_CLEANUP_FAILED 与可重试的 STOP_FAILED；后两者清零前禁止 start，重复 shutdown 必须继续调用 stop。
- APScheduler 的 STOPPED/running 只表示 callback admission 已关闭，不代表 executor、job store 与 shutdown event 已清理；adapter 必须逐阶段记录完成度，异常重试从首个未完成资源继续，全部完成前不得清空应用 registration 或报告成功。
- Runtime 停止必须遵守 dependency graph：dependent 仍为 RUNNING/STOP_FAILED 时，其 dependency 保持运行并返回 deferred failure；dependent 重试成功后才可在同一逆序 pass 中继续停止依赖，且该规则必须传递保护整条依赖链。
- APScheduler 3.x 的 DateTrigger 自动删除与 JobStore shutdown 竞态必须在 scheduler adapter 内收口；不得在业务任务或测试中吞掉后台线程异常。

## 前置与并行

前置 M00。可早期独立实施；与 M13 集成 extension catalog 的调用点时只依赖其 facade。
