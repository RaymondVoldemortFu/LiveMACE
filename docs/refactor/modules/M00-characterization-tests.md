# M00：结构重构行为基线测试

## 交付目标

在移动任何生产代码前，把当前可见行为和金融副作用冻结为自动化测试。本任务不改生产逻辑，只允许增加测试 fixture、fake provider 和测试辅助函数。

## 文件边界

- 修改：`backend/test/`、`backend/tests/`。
- 新增：`backend/tests/characterization/`、`backend/tests/fakes/`。
- 禁止修改：`backend/services/`、`backend/api/`、`backend/database/models.py`。

## TODO

- [ ] 建立不导入 `main:app` 的 SQLite session fixture；每个测试回滚并验证 session 关闭。
- [ ] 建立 fake LLM，能按步骤返回 assistant tool call、tool result 后终止、缺失 `<TRADE_DONE>`、provider error。
- [ ] 建立 fake market provider，覆盖有效价格、`0`、`None`、异常、US closed。
- [ ] 冻结 ReAct、MultiAgent、AdvancedMultiAgent、RuleAware 的 Agent 选择逻辑和账户 flags 解析。
- [ ] 冻结 `execute_trade_tool()` 的 open/close/hold/all_in/close_all、portion/usd/ratio 语义。
- [ ] 断言一次成功工具调用只产生一次订单/成交/资金变化，即使最终文本没有 `<TRADE_DONE>`。
- [ ] 冻结普通 MARKET、LIMIT、取消、pending scheduler 成交的账户/持仓/订单/成交变化。
- [ ] 冻结 baseline buy-hold/grid 的输入输出，不连接真实行情。
- [ ] 冻结 WS `bootstrap`、`switch_account`、`get_snapshot`、`get_asset_curve`、`place_order` 当前消息字段。
- [ ] 冻结 account flag 的 string/bool 输出转换现状。

## 验收

- `uv run pytest tests/characterization -q` 无外部 Redis/MySQL/Docker/LLM 凭据即可运行。
- 每个核心 Agent 至少有一个成功 run fixture；每个交易 operation 至少一个断言。
- 测试断言数据库最终状态，而不只断言 mock 被调用。
- 不改变现有生产文件的 git diff。

## 前置与可并行性

无前置任务。

可拆为 Agent、交易、API/WS、baseline 四个测试子任务；fixture 负责人先冻结公共 fake API。
