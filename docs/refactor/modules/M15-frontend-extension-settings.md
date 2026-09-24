# M15：前端 Agent/Tool/Prompt 扩展设置

## 交付目标

在现有 SettingsDialog 中提供面向普通用户的运行组件配置界面。用户能够查看组件说明、按 schema 编辑参数、验证并保存，不需要知道 Python 类或文件路径。

## 文件边界

- 新增：`frontend/app/lib/api/extensions.ts`、`frontend/app/components/extensions/`、相关 hooks。
- 修改：`components/layout/SettingsDialog.tsx`。
- `frontend/app/lib/api.ts` 只允许临时 re-export，最终由 M23 整理。
- 不改变现有视觉系统或引入大型状态库。

## UI 功能

- Agent selector：id/name/version/description/status；invalid 组件不可选。
- Toolset selector：显示工具名、side effect、capability；交易工具有明确提示。
- Prompt profile selector：显示来源、版本和可预览内容/hash。
- Agent config form：依据 JSON Schema 渲染 string/number/bool/enum/object 基础字段；不支持的 schema 显示 JSON fallback editor。
- Validate/Save：先调用 validate，成功后 PUT；显示字段级错误。

## 完成清单

- [x] 建 generated/typed API DTO，不复制 backend 字段定义到组件。
- [x] 建 `useExtensionCatalog(enabled)`（组件目录为全局资源） 与 `useAccountRuntimeConfig(accountId)`。
- [x] 编辑态与服务端已保存态分离；关闭未保存对话框需确认。
- [x] catalog load_failed/incompatible/disabled 有用户可读说明。
- [x] Prompt preview 只读，显示 profile id/version/hash。
- [x] 保存成功触发现有账户列表和 snapshot refresh，但不在前端假定运行时已切换。
- [x] 保持当前 model/base_url/api_key、memory、rule-aware 设置功能；迁移后由 runtime config 表达的 flag 不重复提交旧字段。

## 验收

- 用户能从 UI 将账户在四个内置 Agent 间切换，下一轮使用所选组件。
- 能启停可选 toolset、选择 Prompt profile、填写 Agent schema 参数。
- 无效配置不能保存；后端详细错误正确落到字段或全局提示。
- `pnpm run build:frontend` 通过，组件无直接 fetch。

## 前置与并行

前置 M14。组件、hooks、schema form 可并行；与 M23 共享 API client 文件时按 README 冲突规则集成。


## WAVE 4 验收记录

见 [WAVE 4 收尾与验收报告](../wave4-implementation-report.md)。Toolset 由 Catalog capability 分组，UI 将组开关展开为现有 `disabled_tools`，不新增持久化组语义。四种 Agent 的下一轮选择通过 HTTP 保存、SQLite 持久化及真实 factory 构建链验收；模型输出由离线测试替身提供。
