# WAVE 4 收尾与验收报告

后续全量重构收尾与真实 LLM 验收见 [整体完成核查](overall-completion-audit.md)。下文保留此前 WAVE 4 阶段的实现和测试记录。

验收日期：2026-09-25。范围：M15 扩展设置、M22 评测与合规、M23 前端数据层，以及 M17 遗留兼容入口清理。

## 修复结果

| 问题 | 完成行为 |
| --- | --- |
| 历史工具 schema 缺失被算作幻觉 | 按历史版本解析；无法解析时返回 unavailable，依赖 schema 的指标为 null，汇总排除该样本并保留不可用计数。历史事件明确记录 TOOL_NOT_FOUND 时仍计入幻觉。 |
| JSON 参数无效仍能保存 | 编辑器保留错误文本，按字段记录错误；全部修正前阻止保存，切换 Agent 或放弃草稿时重置编辑器。 |
| 409 冲突覆盖未保存编辑 | 保留草稿，用户明确选择保留编辑或采用服务端版本后才能继续；更新并发版本号后可重试。 |
| OpenAPI drift 只比较两个 DTO 的字段名 | 从应用完整 OpenAPI 生成 DTO，并逐字比对再生成结果；覆盖字段类型、required、nullable、嵌套结构与 enum。前端 build 执行 tsc。 |

独立审查另发现切换 Agent/Prompt 后旧组件版本绑定残留，已修复并增加真实 React renderer 回归；无关工具版本绑定继续保留。

## 模块收尾

- M15：Catalog、schema 参数、Prompt 预览、校验保存、冲突处理及未保存确认完成。工具按 capability 分组，组开关展开为 `disabled_tools`。模型连接、memory 和 rule-aware 设置接入现有保存流程。
- M22：checkpoint 计算与调度注册分离；评测 data loader 和合规读取使用 repository；judge 使用 LLMClientPort；路由返回 Pydantic DTO。评分公式、规则 JSON 和 Prompt fixture 保持原样。
- M23：移除旧 API facade，组件中的 fetch 迁入 domain client/hook；WS client 集中处理连接、心跳、重连、订阅和解码，snapshot 合并留在 hook。
- M17：删除生产 factory/core/env_wrapper 和旧 Prompt 常量兼容入口，迁移对照 helper 归入 `tests/legacy_fixtures/`。

## 验证证据

- 后端主回归：473 passed，2 integration deselected；随后补充风险快照用例，所属文件 11 passed。合计 474 个不同的后端用例通过。
- 前端：11 passed；`pnpm run build:frontend` 通过，含 `tsc --noEmit` 与 Vite 构建。
- 四种内置 Agent：HTTP validate/save → SQLite 已提交配置 → 生产 worker → 真实 factory 构建，覆盖工具禁用、Prompt 选择及 rule-aware 镜像字段。
- 风险回归：固定 SQLite 快照断言最大回撤 -0.2727、三次连续亏损、尾部阈值 -0.1089，并检查时间过滤。M00 未提供独立 risk 数值 fixture，此处为新增对照数据；原风险计算实现未修改。
- 隔离烟测：真实应用启动/readiness、构建后首页、16 个 HTTP 读取，以及 WS bootstrap/snapshot/ping/curve 全通过；scheduler 保持停止。
- 浏览器仅打开临时烟测首页，确认导航、曲线页面和控件正常渲染。
- 仅使用同一个独立子智能体完成两轮代码审查。首轮问题修复后，第二轮未发现新的可确认 P1/P2；独立运行后端 33 项、前端配置/WS 10 项通过。

自动化使用临时 SQLite、fake provider/模型输出，不调用真实模型或执行真实模型交易。四种 Agent 的测试确认配置传递和 factory 选择；不代表真实模型端到端质量验收。未操作 `alpha-arena-wave3` Compose 栈。

## 重复执行

仓库根目录：

```sh
pnpm run build:frontend
pnpm --dir frontend test
git diff --check
```

backend 目录：

```sh
DATABASE_URL=sqlite:// .venv/bin/python scripts/generate_frontend_types.py --check
DATABASE_URL=sqlite:// .venv/bin/python -m pytest \
  tests/characterization tests/api tests/extensions tests/services/evaluation \
  tests/prompts tests/agents tests/trading tests/tools \
  test/test_routing_quality_score.py test/test_baselines.py \
  test/test_tool_use_step_definition.py test/test_tool_use_dynamic_schema.py \
  -m 'not integration' -q --tb=short
.venv/bin/python scripts/wave4_smoke.py
```

主回归仍有既存的 datetime/websockets 等弃用警告，未导致测试失败。
