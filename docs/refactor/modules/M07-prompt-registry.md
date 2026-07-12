# M07：Prompt Registry、模板与覆盖

## 交付目标

提供无需修改 Python 即可新增、选择和覆盖 Prompt 的运行时；本任务实现装载与渲染，不搬迁现有 Prompt 文案。

## 文件边界

- 新增：`backend/alpha_arena/prompts/{__init__,protocol,registry,loader,renderer,validation,errors}.py`。
- 新增 Prompt index schema 与测试 fixtures。
- 禁止修改：`backend/services/agent/prompts/*.py`、Agent 实现。

## 暴露接口

实现公共规范 `PromptSpec`、`RenderedPrompt`、`PromptProvider`、`PromptResolver`，并提供：

```python
class PromptRegistry:
    def register_provider(self, extension: ExtensionRef, provider: PromptProvider, priority: int) -> None
    def resolve(self, prompt_id: str, version: str | None = None) -> RegisteredPrompt
    def list(self) -> tuple[PromptSpec, ...]
    def freeze(self) -> None

def load_prompt_directory(root: Path, index: Path) -> PromptProvider
```

## Prompt index

```yaml
prompts:
  - id: core.react.system
    version: 1.0.0
    file: react/system.txt
    required_variables: [portfolio, prices, current_time]
    optional_variables:
      memory_block: ""
```

## TODO

- [ ] 文件必须 UTF-8、位于 Prompt root、大小受限；渲染结果也有最大字符数。
- [ ] 模板只允许命名占位符；禁止 attribute traversal、函数调用、include 和任意代码执行。
- [ ] 装载时解析模板并验证 index 声明变量与实际占位符一致。
- [ ] render 缺失/未知变量报 `PromptRenderError`；输出包含 content SHA-256。
- [ ] 实现内置、外部扩展、账户 profile 的确定性优先级；同优先级冲突报错。
- [ ] Registry freeze 后不可增删；resolver 线程安全只读。
- [ ] 提供 `validate_prompt_directory()` 给扩展 CLI 使用。

## 验收

- 用户新增 Prompt 只需 `.txt/.md` 和 index，不写 Python。
- 同一输入、版本和文件生成稳定 hash。
- 路径逃逸、非法模板、变量不匹配、冲突优先级都有测试。
- Prompt runtime 不 import任何 Agent 实现。

## 前置与并行

前置 M01；可与 Agent/Tool/Provider 并行。M08、M13 依赖本任务。

