# M01：公共数据契约包

## 交付目标

建立不依赖 FastAPI、SQLAlchemy、Redis、Docker 和具体 Agent 的 `benchmark.contracts`。所有后续公开 SPI 共享这些不可变 DTO、枚举和错误。

## 文件边界

- 新增：`backend/benchmark/__init__.py`、`backend/benchmark/contracts/{__init__,common,agent,tool,prompt,trade,errors}.py`。
- 修改：`backend/pyproject.toml` 的 package 配置。
- 禁止迁移现有实现；本任务只定义类型和测试。

## 暴露接口

严格实现 `000-public-interface-spec.md` 中：`Market`、`JsonValue`、`ExtensionRef`、全部 View/Context、`AgentRunResult`、`ToolResult`、Prompt DTO、Trade DTO 和公共异常。

额外规则：

- 所有金额和数量字段使用 `Decimal`；时间使用 timezone-aware `datetime`。
- DTO 使用 frozen dataclass；`Mapping`/tuple 替代可变 dict/list 对外暴露。
- `to_jsonable()` 是唯一公共序列化 helper，Decimal 输出字符串，datetime 输出 UTC ISO 8601。
- `parse_bool_like(value)` 作为迁移期 internal helper，公开 DTO 永远输出 bool。

## TODO

- [ ] 建立包和 re-export 清单；`benchmark.contracts.__all__` 只列公开类型。
- [ ] 实现 DTO 构造期校验：空 id、naive datetime、非正 leverage、非法 market 拒绝。
- [ ] 实现稳定 JSON 序列化测试与 snapshot fixture。
- [ ] 加 import-boundary test，禁止 contracts import `services`、`database`、`api`。
- [ ] 在 `pyproject.toml` 确认 wheel 包含 `benchmark`，不再只打包 `main.py`。

## 验收

- `python -c 'import benchmark.contracts'` 在无 `.env`、无数据库时成功且无副作用。
- DTO 单测覆盖不可变性、Decimal、UTC、枚举和错误序列化。
- 公共类型签名与接口规范逐项一致。

## 前置与并行

前置 M00。合并后 M02、M03、M05、M07、M09、M11、M19 可并行。

