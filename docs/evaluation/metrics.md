# 评估指标

本文基于当前代码实现，总结所有指标的数值范围、打分方法与计算公式。

## 一、客观指标（Tool Use Objective Metrics）
来源：`backend/services/evaluation/tool_use_evaluator.py`

### 1) Tool Hallucination Rate
- **含义**：调用了不存在的工具或工具名不合法的比例。
- **数值范围**：`[0, 1]`
- **公式**：  
  `hallucination_rate = hallucinated_calls / total_tool_calls`

### 2) Invalid / No-op Call Rate
- **含义**：无效或无信息增量的调用比例，包含：
  - 工具返回错误或空结果
  - 工具输出格式无效
  - 参数不符合 schema
  - 重复调用相同工具与参数
- **数值范围**：非负值；各类计数可能重叠，结果可超过 `1`，实现不做归一化截断。
- **公式**：  
  `invalid_or_noop_rate = (error_calls + invalid_output_calls + invalid_params_calls + noop_calls) / total_tool_calls`

两项比例在工具调用总数为零时为 `0`。历史工具 schema 不可用时，`schema_status` 为 `unavailable`，`hallucination_rate` 和 `invalid_or_noop_rate` 均为 `null`，并通过 `unavailable_calls` 保留不可评估的调用数。

### 3) Tool Cost / Budget Usage
- **含义**：单位步骤的工具调用成本，当前实现用 “每步平均工具调用次数” 近似。
- **数值范围**：`[0, +∞)`
- **公式**：  
  `tool_calls_per_step = total_tool_calls / total_steps`

### 4) 统计项（用于汇总）
汇总时对各指标输出均值/中位数/方差：
- **均值**：`mean(values)`
- **中位数**：`median(values)`
- **方差**：`pvariance(values)`
  
实现：`descriptive_stats()`（`tool_use_evaluator.py`）。评测脚本汇总时排除 `null`，并在存在不可评估记录时提供 `evaluated_count` 和 `unavailable_count`；整组均不可评估时，均值、中位数和方差均为 `null`。

---

## 二、LLM 评审指标（LLM-as-Judge）
来源：`backend/services/evaluation/llm_tool_judge.py`  
提示词：`backend/services/evaluation/sys_prompts_eval/tool_use_judge.md`

### 评分范围
所有评分均为 0–10 分的连续数值：
- `Tool Relevance Score`
- `Tool Timing / Budgeting Score`
- `Information Coverage Score`
- `Synthesis / Faithfulness Score`

### 打分方法
- 由 LLM 作为评审器，根据 trace 中的推理、工具调用与工具输出进行主观评分。
- 评审输入结构：
  - account 信息（`agent_type`, `model`, `name`）
  - trace steps（`role`, `content`, `tool_calls`, `tool_output`）
- 评审输出为 JSON，不允许额外字段或文本。

### 各指标含义（来自系统提示词）
- **Tool Relevance Score**：工具是否与决策情境和不确定性相关。
- **Tool Timing / Budgeting Score**：工具调用时序与数量是否合理，是否冗余。
- **Information Coverage Score**：工具获取的信息是否覆盖关键决策要点。
- **Synthesis / Faithfulness Score**：最终决策是否忠实使用工具信息，是否幻觉/曲解。

---

## 三、Token 消耗统计（LLM Judge）
来源：`backend/services/evaluation/llm_tool_judge.py`

### 计算方法
- 使用 `tiktoken` 进行近似计数：
  - `prompt_tokens`：对 judge 输入 messages 进行编码计数
  - `completion_tokens`：对 judge 输出文本计数
  - `total_tokens = prompt_tokens + completion_tokens`
- 编码器选择：
  - 优先 `encoding_for_model(model)`
  - 失败时回退到 `cl100k_base`

### 汇总统计
- 在评测脚本中按 `<agent架构, llm model>` 汇总：
  - `prompt_tokens` / `completion_tokens` / `total_tokens`  
  各自输出：均值 / 中位数 / 方差

---

## 四、输出文件结构
来源：`backend/services/evaluation/run_tool_use_eval.py`

### 单条 trace 输出
包含：
- `objective_metrics`：客观指标与统计
- `judge_metrics`：LLM 评审分数与 `token_usage`

### 汇总输出（summary）
按 `<agent_type, model>` 聚合，包含：
- 客观指标统计
- LLM 评分统计
- token 统计

---

## 五、指标范围速览
- **Tool Hallucination Rate**：`[0, 1]`；schema 不可用时为 `null`
- **Invalid / No-op Call Rate**：非负，可超过 `1`；schema 不可用时为 `null`
- **Tool Cost / Budget Usage**：`[0, +∞)`（平均每步调用次数）
- **LLM 评分指标**：`[0, 10]`
- **Token 消耗**：`[0, +∞)`
