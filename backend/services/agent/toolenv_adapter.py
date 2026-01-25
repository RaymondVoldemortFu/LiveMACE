import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from config.tool_config import ToolConfig
from services.agent.tools import Tool, ToolRegistry
from services.agent.toolserver import ToolRequest, ToolServer, change_name, standardize


def _parse_blacklist(raw_value: Optional[Union[str, List[str]]]) -> Set[Tuple[str, ...]]:
    if not raw_value:
        return set()
    entries: Iterable[str]
    if isinstance(raw_value, list):
        entries = raw_value
    else:
        raw_value = raw_value.strip()
        if raw_value.startswith("["):
            try:
                entries = json.loads(raw_value)
            except Exception:
                entries = [raw_value]
        else:
            entries = [p.strip() for p in raw_value.split(",") if p.strip()]
    normalized: Set[Tuple[str, ...]] = set()
    for entry in entries:
        if not entry:
            continue
        if entry.startswith("toolenv__"):
            parts = [p for p in entry.split("__") if p and p != "toolenv"]
        elif entry.startswith("toolenv."):
            parts = [p for p in entry.split(".") if p and p != "toolenv"]
        else:
            parts = [p for p in entry.split("/") if p]
        normalized_parts = tuple(standardize(p) for p in parts)
        if normalized_parts:
            normalized.add(normalized_parts)
    return normalized


def _is_blacklisted(
    blacklist: Set[Tuple[str, ...]],
    category: str,
    tool: str,
    api: Optional[str] = None,
) -> bool:
    cat = standardize(category)
    tool_name = standardize(tool)
    if (cat, tool_name) in blacklist:
        return True
    if api is not None:
        api_name = standardize(api)
        if (cat, tool_name, api_name) in blacklist:
            return True
    return False


def _map_param_type(param_type: str) -> str:
    if not param_type:
        return "string"
    ptype = param_type.strip().upper()
    if ptype in {"NUMBER", "FLOAT", "DOUBLE", "DECIMAL"}:
        return "number"
    if ptype in {"INTEGER", "INT"}:
        return "integer"
    if ptype in {"BOOLEAN", "BOOL"}:
        return "boolean"
    if ptype in {"ARRAY", "LIST"}:
        return "array"
    return "string"


class ToolEnvAdapter:
    def __init__(
        self,
        tools_root: Optional[Union[str, Path]] = None,
        schema_root: Optional[Union[str, Path]] = None,
        rapidapi_key: Optional[str] = None,
        blacklist: Optional[Union[str, List[str]]] = None,
    ):
        self.toolserver = ToolServer(
            tools_root=tools_root,
            schema_root=schema_root,
            rapidapi_key=rapidapi_key,
        )
        self.tools_root = self.toolserver.tools_root
        self.blacklist = _parse_blacklist(blacklist or ToolConfig.TOOLENV_BLACKLIST)

    def register_toolenv_tools(self, registry: ToolRegistry):
        for category_dir in sorted([p for p in self.tools_root.iterdir() if p.is_dir()]):
            category = category_dir.name
            for json_file in sorted(category_dir.glob("*.json")):
                tool_name = json_file.stem
                if _is_blacklisted(self.blacklist, category, tool_name):
                    continue
                meta = self._load_tool_meta(json_file)
                api_list = meta.get("api_list", [])
                for api in api_list:
                    api_name = api.get("name")
                    if not api_name:
                        continue
                    if _is_blacklisted(self.blacklist, category, tool_name, api_name):
                        continue
                    tool = self._build_tool(category, tool_name, api)
                    if tool:
                        registry.register(tool)

    def _load_tool_meta(self, path: Path) -> Dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _build_tool(self, category: str, tool_name: str, api: Dict[str, Any]) -> Optional[Tool]:
        api_name = api.get("name", "")
        if not api_name:
            return None
        func_name = self._build_tool_name(category, tool_name, api_name)
        description = (api.get("description") or "").strip()
        if not description:
            description = f"ToolEnv API: {category}/{tool_name}/{api_name}"

        parameters = self._build_parameters(api)

        def _call_tool(**kwargs):
            request = ToolRequest(
                category=category,
                tool_name=tool_name,
                api_name=api_name,
                tool_input=kwargs,
                strip="none",
            )
            return self.toolserver.run_tool(request)

        return Tool(
            name=func_name,
            description=description,
            parameters=parameters,
            func=_call_tool,
        )

    def _build_tool_name(self, category: str, tool_name: str, api_name: str) -> str:
        cat = standardize(category)
        tool = standardize(tool_name)
        api = change_name(standardize(api_name))
        return f"toolenv__{cat}__{tool}__{api}"

    def _build_parameters(self, api: Dict[str, Any]) -> Dict[str, Any]:
        required = []
        properties: Dict[str, Any] = {}

        for param in api.get("required_parameters", []) or []:
            name = param.get("name")
            if not name:
                continue
            properties[name] = {
                "type": _map_param_type(param.get("type")),
                "description": param.get("description", ""),
            }
            required.append(name)

        for param in api.get("optional_parameters", []) or []:
            name = param.get("name")
            if not name or name in properties:
                continue
            properties[name] = {
                "type": _map_param_type(param.get("type")),
                "description": param.get("description", ""),
            }

        return {"type": "object", "properties": properties, "required": required}


