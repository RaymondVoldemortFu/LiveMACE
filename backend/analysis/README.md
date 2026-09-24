# LiveMACE bench DB Viewer

一个独立的只读 SQLite 查看工具，用于浏览 `alpha_arena_final.sqlite` 中的账号、历史 trace 和任意表数据。

## 启动

在 `backend` 目录复用本项目 uv 环境运行：

```bash
uv run python -m analysis.db_viewer --db ../alpha_arena_final.sqlite --port 8765
```

打开：

```text
http://127.0.0.1:8765
```

默认只绑定 `127.0.0.1`，并以 SQLite `mode=ro` 只读方式打开数据库。

## 功能

- Trace 聊天查看：按 account 选择历史 trace，右侧按 `step_number` 顺序展示完整消息、tool calls、tool output 和 decision 摘要。
- 资产曲线：按多个 account 展示 `asset_curve_snapshots.timeframe = '1h'` 的完整资产变动曲线，不分页、不截断；鼠标悬停时按当前盈利金额排序展示。
- Final 排名：按选中 account 的最新 1h 快照展示最终资产、盈利金额、收益率等排名。
- DB 检索：按表分页浏览，支持全表搜索、列筛选、排序和分页。

