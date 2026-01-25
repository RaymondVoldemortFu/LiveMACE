# Rule-Aware Agent API 文档

## 已实现的API端点

### 1. 规则配置 API (`/api/rules/`)

#### `GET /api/rules/summary`
获取规则摘要统计

**响应示例**:
```json
{
  "total_rules": 13,
  "r0_count": 4,
  "r1_count": 3,
  "r2_count": 6,
  "categories": {
    "r0": {
      "name": "System Hard Constraints",
      "description": "Critical system-level rules that cannot be violated",
      "count": 4
    },
    "r1": {
      "name": "Client Hard Rules",
      "description": "Client-specified mandatory requirements",
      "count": 3
    },
    "r2": {
      "name": "Client Soft Preferences",
      "description": "Client preferences with weighted scoring",
      "count": 6
    }
  }
}
```

#### `GET /api/rules/list`
获取所有规则的简化列表（ID、名称、分类）

**响应示例**:
```json
{
  "total": 13,
  "rules": [
    {
      "id": "R0-01",
      "name": "Maximum Leverage Limit",
      "category": "R0",
      "category_name": "System Hard"
    },
    {
      "id": "R2-03",
      "name": "Sector Diversification",
      "category": "R2",
      "category_name": "Client Soft",
      "weight": 2.0
    }
  ]
}
```

---

### 2. 合规统计 API (`/api/compliance/`)

#### `GET /api/compliance/account/{account_id}/history`
获取账户的合规评估历史记录

**Query参数**:
- `limit` (可选): 每页记录数，默认50，最大500
- `offset` (可选): 偏移量，默认0

**响应示例**:
```json
{
  "total": 128,
  "limit": 50,
  "offset": 0,
  "records": [
    {
      "id": 1,
      "timestamp": "2026-01-22T10:30:00",
      "trace_id": "abc-123",
      "gate_pass": true,
      "s_rule_sat": 0.850,
      "s_audit": 0.900,
      "final_score": 0.875
    }
  ]
}
```

**字段说明**:
- `gate_pass`: 硬规则（R0+R1）是否全部通过
- `s_rule_sat`: R2软规则满意度分数 (0.0-1.0)
- `s_audit`: LLM审计分数 (0.0-1.0)
- `final_score`: 最终合规分数 (0.0-1.0)

---

#### `GET /api/compliance/account/{account_id}/trend`
获取合规趋势数据（用于图表显示）

**Query参数**:
- `period`: 时间粒度，可选值: `day`(30天), `week`(90天), `month`(365天)
- `metric`: 指标类型，可选值:
  - `gate_pass_rate`: 硬规则通过率
  - `final_score`: 最终合规分数
  - `s_rule_sat`: R2软规则满意度
  - `s_audit`: LLM审计分数

**响应示例**:
```json
{
  "period": "day",
  "metric": "final_score",
  "data_points": [
    {
      "date": "2026-01-20",
      "value": 0.820,
      "count": 12
    },
    {
      "date": "2026-01-21",
      "value": 0.850,
      "count": 15
    },
    {
      "date": "2026-01-22",
      "value": 0.810,
      "count": 8
    }
  ]
}
```

---

#### `GET /api/compliance/account/{account_id}/stats`
获取账户的汇总统计信息

**响应示例**:
```json
{
  "total_evaluations": 128,
  "all_time": {
    "gate_pass_rate": 0.953,
    "avg_final_score": 0.845,
    "avg_s_rule_sat": 0.820,
    "avg_s_audit": 0.870,
    "evaluation_count": 128
  },
  "recent_7d": {
    "gate_pass_rate": 0.960,
    "avg_final_score": 0.855,
    "avg_s_rule_sat": 0.830,
    "avg_s_audit": 0.880,
    "evaluation_count": 35
  },
  "llm_audit_stats": {
    "count": 42,
    "avg_score": 0.812,
    "avg_coverage": 4.15,
    "avg_conflict": 4.48
  }
}
```

**字段说明**:
- `all_time`: 全部历史数据的统计
- `recent_7d`: 最近7天的统计（用于对比趋势）
- `llm_audit_stats`: LLM审计的累计统计
  - `avg_score`: 累计平均分数 (0.0-1.0)
  - `avg_coverage`: 累计平均规则覆盖率 (1.0-5.0)
  - `avg_conflict`: 累计平均冲突处理分数 (1.0-5.0)

---

#### `GET /api/compliance/recent-decisions`
获取最近的交易决策及其合规分数

**Query参数**:
- `account_id` (必需): 账户ID
- `limit` (可选): 返回记录数，默认20，最大100

**响应示例**:
```json
{
  "account_id": 1,
  "count": 20,
  "decisions": [
    {
      "trace_id": "abc-123",
      "timestamp": "2026-01-22T10:30:00",
      "operation": "open",
      "symbol": "BTC",
      "leverage": 3,
      "executed": true,
      "compliance": {
        "gate_pass": true,
        "final_score": 0.875,
        "s_audit": 0.900
      }
    }
  ]
}
```

---

## 前端使用场景

### 1. 规则概览页面
- 调用 `GET /api/rules/summary` 显示规则统计卡片
- 调用 `GET /api/rules/list` 显示规则列表表格

### 2. 账户合规仪表板
- 调用 `GET /api/compliance/account/{id}/stats` 显示关键指标
- 显示指标卡片：通过率、平均分数、LLM审计统计

### 3. 合规历史页面
- 调用 `GET /api/compliance/account/{id}/history` 显示历史记录表格
- 支持分页加载

### 4. 合规趋势图表
- 调用 `GET /api/compliance/account/{id}/trend` 获取时间序列数据
- 使用图表库（如 ECharts）绘制折线图
- 可切换不同指标和时间粒度

### 5. 最近活动流
- 调用 `GET /api/compliance/recent-decisions` 显示最近决策列表
- 每条记录显示时间、操作、合规分数
- 点击可展开查看详情（未来扩展）

---

## 前端示例代码

### React/Vue 示例

```typescript
// 获取合规统计
const getComplianceStats = async (accountId: number) => {
  const response = await fetch(`/api/compliance/account/${accountId}/stats`);
  const data = await response.json();
  
  return {
    totalEvals: data.total_evaluations,
    passRate: data.all_time.gate_pass_rate,
    avgScore: data.all_time.avg_final_score,
    llmAudit: data.llm_audit_stats
  };
};

// 获取趋势数据
const getTrendData = async (accountId: number) => {
  const response = await fetch(
    `/api/compliance/account/${accountId}/trend?period=day&metric=final_score`
  );
  const data = await response.json();
  
  return data.data_points.map(point => ({
    x: point.date,
    y: point.value
  }));
};
```

---

## 数据库依赖

这些API依赖以下数据库表：
- `rule_evaluation_results`: 存储每次决策的评估结果
- `accounts`: 存储LLM审计的累计统计（llm_audit_*字段）
- `ai_decision_logs`: 存储交易决策记录

确保已运行 `python backend/update_db_schema.py` 初始化这些表。
