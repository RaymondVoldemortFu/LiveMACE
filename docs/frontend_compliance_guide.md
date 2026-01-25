# Rule-Aware Agent 前端页面使用说明

## 新增页面：Rule Compliance Dashboard (规则合规仪表板)

### 访问方式

在应用侧边栏点击 **盾牌图标 (Shield)** 即可进入规则合规页面。

---

## 页面功能模块

### 1. **规则概览卡片** (Rule Summary Card)
显示系统中配置的所有交易规则：
- **R0 - System Hard**: 4条系统级硬规则（红色）
- **R1 - Client Hard**: 3条客户硬规则（橙色）  
- **R2 - Client Soft**: 6条客户软规则（蓝色）
- 规则优先级说明：R0 > R1 > R2

**数据来源**: `GET /api/rules/summary`

---

### 2. **合规统计卡片** (Compliance Stats Cards)
显示4个关键指标：

#### Gate Pass Rate (硬规则通过率)
- 显示所有决策中通过R0+R1硬规则的百分比
- 包含全时段和最近7天的对比
- 绿色上箭头 ↑ 表示最近表现更好

#### Avg Final Score (平均最终分数)
- 综合合规分数（0.0-1.0）
- 包含全时段和最近7天数据
- 分数越高表示合规性越好

#### Rule Satisfaction (规则满意度)
- R2软规则的平均满意度
- 显示对软规则的遵循程度

#### LLM Audit Score (LLM审计分数)
- 累计平均LLM审计分数
- 包含：
  - **Coverage** (覆盖率): 1-5分，评估规则检查完整性
  - **Conflict** (冲突处理): 1-5分，评估规则冲突解决能力
  - 总审计次数

**数据来源**: `GET /api/compliance/account/{account_id}/stats`

---

### 3. **合规趋势图表** (Compliance Trend Chart)
可视化展示合规指标的时间趋势：

**控制选项**:
- **时间粒度**: Last 30 Days / Last 90 Days / Last Year
- **指标类型**: 
  - Final Score (最终分数)
  - Gate Pass Rate (硬规则通过率)
  - Rule Satisfaction (规则满意度)
  - LLM Audit Score (LLM审计分数)

**图表特性**:
- 蓝色折线图显示趋势
- X轴：日期
- Y轴：分数值（自动缩放）
- 鼠标悬停可查看具体数值（待实现）

**数据来源**: `GET /api/compliance/account/{account_id}/trend`

---

### 4. **最近决策表格** (Recent Decisions Table)
显示最近10条交易决策及其合规情况：

**表格列**:
- **Time**: 决策时间
- **Operation**: 操作类型（open/close/hold）
- **Symbol**: 交易标的
- **Leverage**: 杠杆倍数
- **Gate Pass**: 硬规则是否通过（✓/✗）
- **Final Score**: 最终合规分数
- **LLM Audit**: LLM审计分数
- **Executed**: 是否已执行

**颜色编码**:
- 绿色（≥0.8）：优秀
- 黄色（0.6-0.8）：良好
- 红色（<0.6）：需改进

**数据来源**: `GET /api/compliance/recent-decisions`

---

## 使用场景

### 1. 监控Agent合规性
- 实时查看当前account的规则遵循情况
- 识别合规性下降趋势
- 对比最近7天与全时段表现

### 2. 分析决策质量
- 查看LLM审计的详细评分
- 了解规则覆盖率和冲突处理能力
- 识别哪些决策未通过硬规则

### 3. 优化Agent配置
- 根据趋势图调整Agent参数
- 针对低分决策优化提示词
- 确保符合监管要求

---

## 前端技术栈

### 组件结构
```
frontend/app/components/compliance/
├── ComplianceDashboard.tsx          # 主容器组件
├── ComplianceStatsCards.tsx         # 统计卡片
├── ComplianceTrendChart.tsx         # 趋势图表（Canvas绘制）
├── RecentDecisionsTable.tsx         # 决策表格
└── RuleSummaryCard.tsx              # 规则摘要卡片
```

### API客户端
```
frontend/app/lib/
└── compliance-api.ts                # 合规API封装
```

### 依赖
- **React**: UI框架
- **TypeScript**: 类型安全
- **Lucide Icons**: 图标库
- **Canvas API**: 图表绘制（无第三方依赖）

---

## 数据刷新机制

1. **初始加载**: 页面打开时自动获取所有数据
2. **手动刷新**: 点击右上角"Refresh"按钮
3. **自动更新**: 切换趋势图控制时自动重新获取数据
4. **账户切换**: 切换账户时会自动加载新账户数据

---

## 注意事项

### 1. 数据可用性
- 如果账户从未使用Rule-Aware Agent，会显示"No compliance data available"
- 需要先使用Rule-Aware Agent进行交易，才会生成合规数据

### 2. 性能优化
- 趋势图使用原生Canvas绘制，性能优于第三方图表库
- 历史记录采用分页加载（当前显示最近10条）
- API请求会缓存在组件状态中

### 3. 响应式设计
- 支持桌面端和移动端
- 卡片布局自适应屏幕宽度
- 表格在小屏幕上可横向滚动

---

## 未来扩展功能

以下功能已预留接口，待实现：
- [ ] 违规记录详情查询
- [ ] 规则详细信息弹窗
- [ ] 决策详情页面（点击trace_id查看完整推理过程）
- [ ] 导出合规报告（PDF/Excel）
- [ ] 合规评分排行榜
- [ ] 实时告警（规则违规时推送通知）
- [ ] 趋势图交互增强（鼠标悬停显示详情）

---

## 故障排查

### 问题1: 显示"Failed to load compliance data"
**可能原因**:
- 后端服务未启动
- 数据库未初始化（缺少`rule_evaluation_results`表）
- 账户ID无效

**解决方案**:
```bash
# 确保后端服务运行
cd backend
python main.py

# 检查数据库schema
python update_db_schema.py
```

### 问题2: 趋势图显示"No trend data available"
**可能原因**:
- 时间范围内无数据
- 该账户使用时间较短

**解决方案**:
- 切换到更长的时间范围（Last Year）
- 使用Rule-Aware Agent进行更多交易生成数据

### 问题3: LLM审计统计显示"No LLM audit data"
**可能原因**:
- Agent创建时未启用`enable_llm_audit=True`
- 未传入`account_id`参数

**解决方案**:
- 确保创建Agent时正确配置：
```python
agent = RuleAwareAgent(
    llm=llm_client,
    tools=tools,
    rule_engine=rule_engine,
    enable_llm_audit=True,   # 必须启用
    account_id=account.id     # 必须传入
)
```
