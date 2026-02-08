from __future__ import annotations

import importlib.util
import json
import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from .tools import Tool

logger = logging.getLogger(__name__)

PUBLIC_APIS_DIR = Path(__file__).resolve().parent / "public-apis"
TOOLS_SCHEMA_PATH = PUBLIC_APIS_DIR / "tools_schema.json"
API_SERVER_PATH = PUBLIC_APIS_DIR / "api_server.py"


@lru_cache(maxsize=1)
def _load_tools_schema() -> List[Dict[str, Any]]:
    if not TOOLS_SCHEMA_PATH.is_file():
        logger.warning("public-apis tools_schema.json not found: %s", TOOLS_SCHEMA_PATH)
        return []
    try:
        with TOOLS_SCHEMA_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning("Failed to load public-apis tools schema: %s", exc)
        return []
    if not isinstance(data, list):
        logger.warning("Invalid tools schema format: expected list, got %s", type(data))
        return []
    return data


@lru_cache(maxsize=1)
def _load_api_server_module():
    if not API_SERVER_PATH.is_file():
        raise FileNotFoundError(f"api_server.py not found at {API_SERVER_PATH}")
    spec = importlib.util.spec_from_file_location("public_apis_api_server", str(API_SERVER_PATH))
    if spec is None or spec.loader is None:
        raise ImportError("Failed to create spec for public api_server")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_public_api_func(name: str):
    def _call(**kwargs):
        api_server = _load_api_server_module()
        return api_server._run_api(name, kwargs)
    return _call


def register_public_api_tools(registry, limit: Optional[int] = None) -> int:
    """
    Register public-apis tools into ToolRegistry using tools_schema.json.
    Returns number of registered tools.
    """
    tools_schema = _load_tools_schema()
    if not tools_schema:
        return 0

    count = 0
    for entry in tools_schema:
        if not isinstance(entry, dict) or entry.get("type") != "function":
            continue
        function_block = entry.get("function") or {}
        name = function_block.get("name")
        if not name:
            continue
        if hasattr(registry, "tools") and name in registry.tools:
            logger.warning("Tool name already exists, skipping public api tool: %s", name)
            continue

        description = function_block.get("description") or f"Call the {name} public API."
        parameters = function_block.get("parameters") or {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }

        registry.register(
            Tool(
                name=name,
                description=description,
                parameters=parameters,
                func=_make_public_api_func(name),
                metadata={"tier": "noise"},
            )
        )
        count += 1
        if limit is not None and count >= limit:
            break
    return count
