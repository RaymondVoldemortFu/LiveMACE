# M02：扩展 Manifest 与静态校验

## 交付目标

让第三方通过一个声明文件描述 Agent、Tool、Prompt 和所需 capability；本任务只解析和校验，不 import 扩展 Python。

## 文件边界

- 新增：`backend/alpha_arena/extensions/{__init__,manifest,validation,paths}.py`。
- 新增：`backend/tests/extensions/test_manifest_*.py`、`backend/alpha_arena/extensions/schema/manifest-v1.json`。
- 禁止修改 bootstrap、Agent factory、Tool registry。

## 暴露接口

```python
def load_manifest(path: Path) -> ExtensionManifest
def validate_manifest(manifest: ExtensionManifest, extension_root: Path) -> ValidationReport
def validate_extension_directory(root: Path) -> ValidationReport
```

`ExtensionManifest` 字段与公共规范一致；`ValidationReport` 包含 `valid`、按字段排序的 errors/warnings，不抛出首错即停。

## TODO

- [ ] 定义并发布 manifest-v1 JSON Schema。
- [ ] 校验 `api_version == 1`、id 规则、SemVer、Python requires、entrypoint `module:attribute` 格式。
- [ ] 校验 component id 全局格式和扩展内无重复。
- [ ] 所有相对路径 resolve 后必须仍位于 extension root；拒绝绝对路径、symlink 逃逸和 `..`。
- [ ] 读取 YAML 时限制文件大小、alias 数量和嵌套深度。
- [ ] config schema、Prompt index 文件必须存在且是可解析 JSON/YAML。
- [ ] 校验 capability 只来自系统已知字符串；未知项报告 error。
- [ ] 提供 `python -m alpha_arena.extensions.validate <dir>`，成功为 0，失败为 2。

## 验收

- fixtures 覆盖合法扩展、未知 api version、重复 id、坏 SemVer、路径逃逸、坏 schema 和未知 capability。
- 静态校验过程不 import manifest 中声明的模块。
- CLI 输出同时支持人类可读文本和 `--json`。

## 前置与并行

前置 M01。可与 Agent/Tool/Prompt runtime 并行；M13 依赖本任务。

