#!/usr/bin/env python3
"""Generate tools_schema.json (OpenAI-compatible) from available APIs."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api_server import get_openai_tools

def main():
    tools = get_openai_tools()
    out_path = os.path.join(ROOT, "tools_schema.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(tools, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(tools)} tools to {out_path}")

if __name__ == "__main__":
    main()
