# M10：单一 Agent 决策编排与删除 Legacy JSON

## 交付目标

把自动 Agent 交易轮次收敛为 `AgentRuntime -> ToolInvoker -> TradeCommandGateway`，并彻底删除 Legacy JSON 决策接口。保持调度周期、账户并发、trace、工具执行和最终交易行为不变。

## 文件边界

- 主改：`backend/services/trading_commands.py`、`backend/services/ai_decision_service.py`、`backend/config/agent_config.py`、`backend/services/auto_trader.py`。
- 新增：`backend/alpha_arena/application/decisions/{service,selection,context_builder}.py`。
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
    async def run(self, request: RunDecisionRound) -> DecisionRoundResult

@dataclass(frozen=True)
class RunDecisionRound:
    account_ids: tuple[int, ...] | None
    max_concurrency: int
    trigger: str
```

## TODO

- [ ] 将账户加载、价格准备、context 构建、并发执行、结果汇总拆为私有协作者。
- [ ] 每个 worker 只通过 UoW 读取快照，关闭 session 后再运行 Agent；Tool/Gateway 自行开事务。
- [ ] 保留 `_ai_trade_run_lock` 的非重叠语义，或封装为等价 `DecisionRoundLock`。
- [ ] decision round id 贯穿 context、tool cache、trace 和结果。
- [ ] Agent 未执行交易时只记录 HOLD/失败，不存在上层代执行。
- [ ] baseline 继续使用独立 `place_baseline_driven_order()`，不进入 Agent registry。
- [ ] `auto_trader.py` 只 re-export 新 service entrypoint，随后清理重复 import。

## 验收

- `rg 'call_ai_for_decision|USE_AGENT|legacy_json' backend --glob '*.py'` 无生产命中。
- 自动交易成功、HOLD、Agent error、工具交易成功、多账户并发结果与 M00 等价。
- 一次 tool call 最多产生一次 trade，数据库断言通过。
- scheduler 调用签名可以保留同步 facade，但内部单一实现。

## 前置与并行

前置 M04、M06、M08、M11、M19。它是高冲突集成任务，不与其他任务同时修改两个主文件。

