# M13：扩展发现、装载与 Catalog

## 交付目标

实现系统启动时的确定性扩展发现和组件注册，向应用层提供只读 catalog 与健康状态。扩展错误不得污染其他扩展或静默覆盖内置组件。

## 文件边界

- 新增：`backend/benchmark/extensions/{discovery,loader,catalog,runtime_config}.py`。
- 新增：`backend/benchmark/builtin/extension/alpha-arena-extension.yaml`。
- 修改 bootstrap 的装载调用由 M18 集成；本任务提供 facade。

## 配置

```text
ALPHA_ARENA_EXTENSION_DIRS=/path/a:/path/b
ALPHA_ARENA_DISABLED_EXTENSIONS=id1,id2
ALPHA_ARENA_ALLOWED_CAPABILITIES=market.read,...
```

## 暴露接口

```python
class ExtensionCatalog:
    def list_extensions(self) -> tuple[ExtensionStatus, ...]
    def list_agents(self) -> tuple[AgentDescriptor, ...]
    def list_tools(self) -> tuple[ToolSpec, ...]
    def list_prompt_profiles(self) -> tuple[PromptProfileDescriptor, ...]
    def validate_account_config(self, config: AccountRuntimeConfigDTO) -> ValidationReport

def build_extension_runtime(settings: ExtensionSettings) -> ExtensionRuntime
```

## TODO

- [ ] 发现顺序固定：内置 -> 配置目录按规范化绝对路径排序；不递归扫描未知深度。
- [ ] 先静态校验所有 manifest，再 import 合法且启用的 entrypoint。
- [ ] 每个扩展在独立错误边界装载；状态为 loaded/disabled/invalid/load_failed/incompatible。
- [ ] 注册 Agent/Tool/Prompt 时检查 component id/version 冲突和 capability 授权。
- [ ] 完成后 freeze 三个 registry；运行中不支持热加载，避免交易轮次切换实现。
- [ ] catalog 输出来源路径只用于管理员，不通过公共 API 暴露宿主绝对路径。
- [ ] 内置组件也通过相同 manifest 和 loader 注册，不走隐藏硬编码通道。

## 验收

- 一个合法外部扩展可同时贡献 Agent、Tool 和 Prompt。
- 一个坏扩展不会阻止内置扩展装载；但引用坏扩展的账户 invalid。
- 组件冲突、禁用、API 不兼容、capability 未授权均有稳定状态和错误。
- 同一配置重复启动得到相同 catalog 顺序和版本选择。

## 前置与并行

前置 M02、M03、M05、M07。M12/M14/M17 依赖本任务。

