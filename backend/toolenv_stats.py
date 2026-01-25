import argparse
import json
from pathlib import Path


def iter_tool_json_files(tools_root: Path):
    for category_dir in sorted([p for p in tools_root.iterdir() if p.is_dir()]):
        for json_file in sorted(category_dir.glob("*.json")):
            yield category_dir.name, json_file


def load_api_count(json_path: Path) -> int:
    try:
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data.get("api_list", []))
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(description="统计 ToolEnv 工具与 API 数量")
    parser.add_argument(
        "--toolenv-root",
        default="backend/services/agent/toolenv",
        help="ToolEnv 根目录（可传绝对路径或相对路径）",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="输出按分类统计信息",
    )
    args = parser.parse_args()

    toolenv_root = Path(args.toolenv_root).resolve()
    tools_root = toolenv_root / "tools"

    if not tools_root.exists():
        print(f"tools 目录不存在: {tools_root}")
        return

    category_stats = {}
    total_tools = 0
    total_apis = 0

    for category, json_file in iter_tool_json_files(tools_root):
        api_count = load_api_count(json_file)
        total_tools += 1
        total_apis += api_count
        stats = category_stats.setdefault(category, {"tools": 0, "apis": 0})
        stats["tools"] += 1
        stats["apis"] += api_count

    categories = sorted(category_stats.keys())
    print(f"分类数量: {len(categories)}")
    print(f"工具数量: {total_tools}")
    print(f"API数量: {total_apis}")

    if args.details:
        for category in categories:
            stats = category_stats[category]
            print(f"- {category}: tools={stats['tools']}, apis={stats['apis']}")


if __name__ == "__main__":
    main()

