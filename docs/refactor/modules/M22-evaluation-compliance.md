# M22：Evaluation 与 Compliance 模块接口整理

## 交付目标

让在线 checkpoint、离线 tool/LLM judge、rule evaluation 和 compliance API 通过明确 repository、DTO 和 runtime event 读取数据；保持指标公式、规则文件和榜单结果不变。

## 文件边界

- 修改：`backend/services/evaluation/*`、`backend/services/agent/rule_aware` 中 auditor/validator、`api/evaluation_routes.py`、`api/compliance_routes.py`、`api/rule_routes.py`。
- 新增：`backend/benchmark/application/evaluation/`、`compliance/`。
- 不改 metric prompt 文案、评分公式、规则 JSON 内容。

## 接口

```python
class CheckpointService:
    def run_due(self, intervals: tuple[int, ...], now: datetime) -> CheckpointRunResult

class EvaluationService:
    def evaluate_trace(self, request: EvaluateTraceRequest) -> EvaluationResultDTO

class ComplianceService:
    def evaluate(self, request: ComplianceRequest) -> ComplianceResultDTO
```

DTO 必含 account、trace、round、component versions；旧数据缺字段时为 null，不猜测。

## 完成清单

- [x] 将 checkpoint 计算与 scheduler registration 分开，保留 `(account, interval, period_end)` 幂等。
- [x] data loader 改用 repositories/views，不直接依赖 Agent registry 或 route。
- [x] LLM judge 通过 `LLMClientPort`，provider test 标 integration；本地 evaluator deterministic。
- [x] Tool schema 通过 Extension Catalog 按 trace 中 tool version 解析，无法解析时返回 explicit unavailable。
- [x] rule engine 装载规则与 Agent Prompt 解耦；规则 JSON 内容不变。
- [x] compliance/evaluation routes 只调用 service，输出 Pydantic DTO。
- [x] 运行 M00 行为基线及 checkpoint、tool-use、compliance 回归；补充固定 SQLite 快照的非零 risk 数值断言。

## 验收

- 所有现有 evaluation/routing quality/baseline tests 通过且数值 fixture 不变。
- 同一 checkpoint 重跑不新增行。
- 未安装历史扩展时 trace 评测报告 component unavailable，不崩溃、不用当前版本代替。
- route 无 ORM 查询，敏感 LLM 配置不进入结果。

## 前置与并行

前置 M16、M19、M21。Checkpoint、tool-use judge、rule compliance 三支可并行。


## WAVE 4 验收记录

见 [WAVE 4 收尾与验收报告](../wave4-implementation-report.md)。风险、规则评分公式和规则 JSON 保持原样。M00 没有独立 risk 数值 fixture，本轮补充固定快照验证回撤、单期涨跌、连续亏损、尾部风险及时间边界。历史 schema 不可用时显式返回 unavailable；历史事件明确记录 `TOOL_NOT_FOUND` 的调用仍计入幻觉。
