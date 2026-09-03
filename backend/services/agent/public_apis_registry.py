from __future__ import annotations

import importlib.util
import json
import logging
import sys
import copy
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


def _normalize_json_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize generated JSON schema to satisfy strict tool validators.
    Specifically ensure every array-typed schema has an `items` field.
    """
    normalized = copy.deepcopy(schema)

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            node_type = node.get("type")
            is_array_type = (
                node_type == "array"
                or (isinstance(node_type, list) and "array" in node_type)
            )
            if is_array_type and "items" not in node:
                # Keep items broad to avoid changing runtime behavior.
                node["items"] = {}

            for key in ("properties",):
                sub = node.get(key)
                if isinstance(sub, dict):
                    for value in sub.values():
                        _walk(value)

            for key in ("items", "additionalProperties"):
                sub = node.get(key)
                if isinstance(sub, (dict, list)):
                    _walk(sub)

            for key in ("anyOf", "allOf", "oneOf"):
                sub = node.get(key)
                if isinstance(sub, list):
                    for value in sub:
                        _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(normalized)
    return normalized


def register_public_api_tools(
    registry,
    limit: Optional[int] = None,
    *,
    account_id: int = 1,
    trace_id: Optional[str] = None,
) -> int:
    """
    Register public-apis tools into ToolRegistry using tools_schema.json.
    Returns number of registered tools.
    """
    from benchmark.builtin.tools.legacy import register_legacy_tools
    from benchmark.builtin.tools.public_api import PublicApiToolsProvider

    provider = PublicApiToolsProvider(limit=limit)
    return register_legacy_tools(
        registry,
        provider.list_tools(),
        account_id=account_id,
        trace_id=trace_id,
        metadata={"tier": "noise", "source": "public-apis"},
    )
