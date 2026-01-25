import argparse
import json
import os
import sys

sys.path.append(os.path.dirname(__file__))

from services.agent.toolserver import ToolRequest, ToolServer


def _print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="ToolBench ToolEnv 调试工具")
    parser.add_argument("--category", help="工具分类，例如 Social")
    parser.add_argument("--tool-name", help="工具名称，例如 quotes_api")
    parser.add_argument("--api-name", help="API 名称，例如 get_random_quote")
    parser.add_argument("--tool-input", default="{}", help="JSON 格式的入参字符串")
    parser.add_argument("--strip", default="none", choices=["none", "filter", "random"], help="返回裁剪策略")
    parser.add_argument("--rapidapi-key", help="临时覆盖 RapidAPI Key")
    parser.add_argument("--list-categories", action="store_true", help="列出所有分类")
    parser.add_argument("--list-tools", action="store_true", help="列出分类下的工具")
    parser.add_argument("--list-apis", action="store_true", help="列出工具下的 API")

    args = parser.parse_args()

    server = ToolServer(rapidapi_key=args.rapidapi_key)

    if args.list_categories:
        _print_json(server.list_categories())
        return

    if args.list_tools:
        if not args.category:
            _print_json({"error": "list-tools 需要 --category"})
            return
        _print_json(server.list_tools(args.category))
        return

    if args.list_apis:
        if not args.category or not args.tool_name:
            _print_json({"error": "list-apis 需要 --category 和 --tool-name"})
            return
        _print_json(server.list_apis(args.category, args.tool_name))
        return

    if not args.category or not args.tool_name or not args.api_name:
        _print_json({"error": "运行工具需要 --category --tool-name --api-name"})
        return

    request = ToolRequest(
        category=args.category,
        tool_name=args.tool_name,
        api_name=args.api_name,
        tool_input=args.tool_input,
        strip=args.strip,
    )
    result = server.run_tool(request)
    _print_json(result)


if __name__ == "__main__":
    main()

