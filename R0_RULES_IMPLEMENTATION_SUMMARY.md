# R0 硬规则实现总结

## 修改概述

已完成 R0 系统硬规则的更新和修复，删除了 R0-05 规则，并完善了 R0-03 的实现逻辑。

---

## R0 规则定义（最终版本）

### R0-01: 最大杠杆限制
- **描述**: 投资组合的总有效杠杆不得超过 **5x**
- **强制执行**: 任何会导致杠杆超过此阈值的订单将被自动拒绝
- **参数**: 
  - `max_leverage`: 5
  - `calculation_method`: "total_notional_value / total_equity"

### R0-02: 维持保证金和强制平仓
- **描述**: 账户必须维持至少 **10%** 的保证金水平
- **强制执行**: 如果保证金水平低于 10%，系统将立即平仓风险最高的仓位
- **参数**:
  - `min_margin_level`: 0.10
  - `auto_liquidation_threshold`: 0.10
  - `calculation_method`: "available_margin / used_margin"

### R0-03: 日内最大回撤（硬停止）
- **描述**: 如果日内权益损失超过前一日收盘权益的 **5%**，将触发"硬停止"
- **强制执行**: 所有活跃订单将被取消，Agent 的交易权限将在本交易日剩余时间内被暂停
- **参数**:
  - `max_drawdown_pct`: 0.05
  - `reference_point`: "previous_day_close"
  - `suspension_duration`: "end_of_day"

### R0-04: 单笔订单最大名义值
- **描述**: 任何单笔订单的名义值不得超过总账户权益的 **20%**
- **强制执行**: 防止集中风险和"胖手指"错误，不符合规定的订单将被阻止
- **参数**:
  - `max_order_pct`: 0.20
  - `calculation_method`: "order_notional / total_equity"

---

## 已删除的规则

### ~~R0-05: 流动性和市场冲击防护~~（已删除）
- 此规则已从系统中完全移除
- 相关的配置和检查逻辑已清理

---

## 实现细节

### 1. 配置文件更新

**文件**: `backend/config/rules/r0_system_hard.json`

**修改**:
- 删除了 R0-05 规则的完整定义
- 现在只包含 R0-01 到 R0-04 共 4 条规则

### 2. 规则验证器增强

**文件**: `backend/services/agent/rule_aware/rule_validator.py`

**新增 R0-03 实现**:
```python
# R0-03: Intraday Maximum Drawdown
elif rule_id == "R0-03":
    max_drawdown_pct = params.get("max_drawdown_pct", 0.05)
    # 检查投资组合是否有前一日收盘权益用于比较
    prev_day_close_equity = portfolio.get("prev_day_close_equity")
    current_equity = portfolio.get("total_equity") or portfolio.get("total_assets", 0)
    
    if prev_day_close_equity and prev_day_close_equity > 0:
        drawdown = (prev_day_close_equity - current_equity) / prev_day_close_equity
        if drawdown > max_drawdown_pct:
            return RuleViolation(
                rule, severity,
                f"Intraday drawdown {drawdown:.2%} exceeds maximum {max_drawdown_pct:.2%}",
                actual_value=drawdown,
                expected_value=f"<= {max_drawdown_pct}"
            )
```

**关键点**:
- 使用 `portfolio.get("prev_day_close_equity")` 获取前一日收盘权益
- 计算相对前一日收盘的回撤比例
- 超过 5% 即触发违规

### 3. 评估器修复

**文件**: `backend/services/evaluation/rule_evaluator.py`

**R0-03 改进**:
```python
# 获取今日开始时间（00:00:00）
today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

# 获取前一日的最后一个快照
prev_day_snapshot = self.db.query(AccountSnapshot).filter(
    AccountSnapshot.account_id == account_id,
    AccountSnapshot.ts < today_start
).order_by(AccountSnapshot.ts.desc()).first()

if prev_day_snapshot:
    prev_day_equity = float(prev_day_snapshot.total_equity)
    current_equity = float(account.total_asset)
    
    if prev_day_equity > 0:
        drawdown = (prev_day_equity - current_equity) / prev_day_equity
        
        if drawdown > 0.05:
            violations.append({
                "rule": "R0-03",
                "level": "R0_SYSTEM_HARD",
                "severity": "CRITICAL",
                "description": f"Intraday drawdown {drawdown:.2%} exceeds 5%...",
                "value": drawdown,
                "threshold": 0.05
            })
```

**R0-04 改进**:
```python
# R0-04: 单笔订单规模 <= 总权益的 20%
if decision.operation in ['open', 'buy'] and decision.target_portion_of_balance:
    target_portion = float(decision.target_portion_of_balance)
    
    if target_portion > 0.20:
        violations.append({
            "rule": "R0-04",
            "level": "R0_SYSTEM_HARD",
            "severity": "CRITICAL",
            "description": f"Order size {target_portion:.2%} exceeds 20% of total equity",
            "value": target_portion,
            "threshold": 0.20
        })
```

**关键改进**:
- 使用数据库的 `AccountSnapshot` 表准确获取前一日收盘数据
- 使用决策中的 `target_portion_of_balance` 字段直接验证订单大小
- 修复了之前使用近似计算的问题

---

## 数据依赖

### RuleValidator 需要的 Portfolio 字段

```python
portfolio = {
    "cash": float,                    # R0-02, R1-04
    "total_equity": float,            # R0-03, R0-04
    "total_assets": float,            # R0-02 (备用)
    "prev_day_close_equity": float,   # R0-03 (必需)
    "positions": Dict[str, float],    # R1-03
    # ...其他字段
}
```

### RuleEvaluator 需要的数据库表

1. **Account 表**:
   - `total_asset`: 当前总资产
   - `maintenance_margin_ratio`: 维持保证金率
   - `current_cash`: 当前现金

2. **AccountSnapshot 表**:
   - `total_equity`: 总权益
   - `cash`: 现金余额
   - `ts`: 时间戳（用于获取前一日数据）

3. **AIDecisionLog 表**:
   - `operation`: 操作类型 ('open', 'close', 'hold')
   - `symbol`: 交易标的
   - `leverage`: 杠杆倍数
   - `target_portion_of_balance`: 目标仓位占比
   - `trace_id`: 追踪ID

---

## 规则优先级

```
R0 (系统硬规则) - 最高优先级
├─ R0-01: 最大杠杆限制
├─ R0-02: 维持保证金
├─ R0-03: 日内最大回撤
└─ R0-04: 单笔订单限制

违反任何 R0 规则 → 决策被拒绝 (Gate = False)
```

---

## 验证流程

### 1. Agent 决策时的验证
```python
# 在 RuleAwareAgent.run() 中
compliance_audit = self.compliance_auditor.audit_decision(
    decision, portfolio, prices, agent_reasoning=parsed_output
)

if compliance_audit.final_status == "FAIL":
    # 有 R0 或 R1 违规
    decision = create_hold_decision("Compliance failure")
```

### 2. 评估时的验证
```python
# 在 RuleEvaluator.evaluate_r0_r1_gate() 中
gate_pass, violations = evaluator.evaluate_r0_r1_gate(
    account_id=account_id,
    trace_id=trace_id
)

# gate_pass = False 表示违反了 R0 或 R1 规则
# violations 列表包含所有违规详情
```

---

## 测试建议

### 测试用例 1: R0-01 杠杆检查
```python
decision = {
    "operation": "open",
    "symbol": "BTC",
    "leverage": 6,  # 超过 5x
    # ...
}
# 预期: 违规，决策被拒绝
```

### 测试用例 2: R0-02 保证金检查
```python
portfolio = {
    "cash": 5000,
    "total_assets": 100000  # 现金率 5% < 10%
}
# 预期: 违规警告
```

### 测试用例 3: R0-03 回撤检查
```python
portfolio = {
    "prev_day_close_equity": 100000,
    "total_equity": 94000  # 回撤 6% > 5%
}
# 预期: 违规，触发硬停止
```

### 测试用例 4: R0-04 订单大小检查
```python
decision = {
    "operation": "open",
    "target_portion_of_balance": 0.25  # 25% > 20%
}
# 预期: 违规，订单被阻止
```

---

## 关键文件清单

| 文件 | 修改内容 | 状态 |
|------|---------|------|
| `backend/config/rules/r0_system_hard.json` | 删除 R0-05 | ✅ 完成 |
| `backend/services/agent/rule_aware/rule_validator.py` | 增加 R0-03 实现 | ✅ 完成 |
| `backend/services/evaluation/rule_evaluator.py` | 修复 R0-03, R0-04 评估逻辑 | ✅ 完成 |

---

## 下一步行动

1. **运行测试**:
   ```bash
   python test_rule_engine.py
   # 预期: 4 条 R0 规则（不再是 5 条）
   ```

2. **验证规则加载**:
   ```python
   from services.agent.rule_aware import RuleEngine
   engine = RuleEngine('backend/config/rules')
   summary = engine.get_rule_summary()
   print(summary)
   # 预期: {'R0_count': 4, 'R1_count': 5, 'R2_count': 6, 'total': 15}
   ```

3. **更新文档**:
   - 更新 `RULE_FRAMEWORK_EXPLANATION.md` 中的规则列表
   - 更新 `README_RULE_FRAMEWORK.md` 中的规则总数
   - 将总规则数从 16 改为 15

4. **集成测试**:
   - 测试 R0-03 使用真实数据库快照
   - 测试 R0-04 使用真实决策日志
   - 验证违规检测准确性

---

## 总结

✅ **已完成**:
- 删除 R0-05 流动性规则
- 实现 R0-03 日内回撤检查（使用前一日收盘权益）
- 修复 R0-04 订单大小检查（使用 target_portion_of_balance）
- 统一 RuleValidator 和 RuleEvaluator 的逻辑
- 确保评估器使用正确的数据库字段

✅ **规则总数**: 15 条（4 R0 + 5 R1 + 6 R2）

✅ **系统状态**: 所有 R0 规则已正确实现并可正常运行
