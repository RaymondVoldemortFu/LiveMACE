# M10：单一 Agent 决策编排与删除 Legacy JSON

## 交付目标

把自动 Agent 交易轮次收敛为 `AgentRuntime -> ToolInvoker -> TradeCommandGateway`，并彻底删除 Legacy JSON 决策接口。保持调度周期、账户并发、trace、工具执行和最终交易行为不变。

## 文件边界

- 主改：`backend/services/trading_commands.py`、`backend/services/ai_decision_service.py`、`backend/config/agent_config.py`、`backend/services/auto_trader.py`。
- 新增：`backend/benchmark/application/decisions/{service,selection,context_builder}.py`。
- 禁止改交易计算、Prompt 文案和 provider 行为。

## 删除清单

- `call_ai_for_decision()` 及其 Prompt/JSON parser/retry 代码。
- `AgentConfig.USE_AGENT`。
- `_process_account_decision_payload()` 中“未执行 decision 再下单”的 legacy 分支。
- `protocol == "tool"` / `executed_trades` 的重复执行猜测逻辑；新 `AgentRunResult` 天生只表示已执行引用。
- legacy JSON 专用测试和文档；替换为“Legacy 已删除”导入/符号不存在测试。

## 暴露的内部应用接口

```python
class DecisionRoundService:
    def run(self, request: RunDecisionRound) -> DecisionRoundResult

@dataclass(frozen=True)
class RunDecisionRound:
    account_ids: tuple[int, ...] | None
    max_concurrency: int
    trigger: str
```

## TODO

- [ ] 将账户加载、价格准备、context 构建、线程池提交、结果汇总拆为私有协作者。
- [ ] 保留当前 `ThreadPoolExecutor` + `as_completed()` 的账户级并发模型，`AGENT_MAX_CONCURRENCY` 继续决定 worker 数量。
- [ ] 一个 account id 对应一个 worker；worker 内同步执行 `AgentRuntime.run()`，系统不创建 asyncio task。
- [ ] 每个 worker 使用独立 UoW/session，禁止跨 worker 共享；session 的具体关闭点必须保证 Agent 所需同步工具可正常工作，并在 `finally` 释放。交易 Gateway 使用自己的明确写事务。
- [ ] 单个 Agent 内 LLM、工具和交易调用保持同步顺序；系统不调度 Agent 内部并发。
- [ ] 保留 `_ai_trade_run_lock` 的非重叠语义，或封装为等价 `DecisionRoundLock`。
- [ ] decision round id 贯穿 context、tool cache、trace 和结果。
- [ ] Agent 未执行交易时只记录 HOLD/失败，不存在上层代执行。
- [ ] baseline 继续使用独立 `place_baseline_driven_order()`，不进入 Agent registry。
- [ ] `auto_trader.py` 只 re-export 新 service entrypoint，随后清理重复 import。

## 验收

- `rg 'call_ai_for_decision|USE_AGENT|legacy_json' backend --glob '*.py'` 无生产命中。
- 自动交易成功、HOLD、Agent error、工具交易成功、多账户并发结果与 M00 等价。
- 测试证明不同账户可以并行、单账户工具调用不重叠、每个 worker session 均关闭。
- 一次 tool call 最多产生一次 trade，数据库断言通过。
- scheduler 调用签名可以保留同步 facade，但内部单一实现。

## 前置与并行

前置 M04、M06、M08、M11、M19。它是高冲突集成任务，不与其他任务同时修改两个主文件。
