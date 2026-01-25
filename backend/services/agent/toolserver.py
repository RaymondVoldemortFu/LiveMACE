import importlib.util
import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from config.tool_config import ToolConfig


def standardize(value: str) -> str:
    if value is None:
        return ""
    buf = []
    for ch in value.strip().lower():
        if ch.isalnum():
            buf.append(ch)
        else:
            buf.append("_")
    result = "".join(buf)
    while "__" in result:
        result = result.replace("__", "_")
    return result.strip("_")


def change_name(value: str) -> str:
    name = standardize(value)
    if not name:
        return name
    if name[0].isdigit():
        return f"_{name}"
    return name


@dataclass
class ToolRequest:
    category: str
    tool_name: str
    api_name: str
    tool_input: Union[str, Dict[str, Any]]
    strip: str = "none"


class ToolServer:
    def __init__(
        self,
        tools_root: Optional[Union[str, Path]] = None,
        schema_root: Optional[Union[str, Path]] = None,
        rapidapi_key: Optional[str] = None,
    ):
        base_root = self._resolve_path(
            ToolConfig.TOOLENV_ROOT or (Path(__file__).resolve().parent / "toolenv")
        )
        self.tools_root = self._resolve_path(
            tools_root or ToolConfig.TOOLENV_TOOLS_ROOT or (base_root / "tools")
        )
        self.schema_root = self._resolve_path(
            schema_root or ToolConfig.TOOLENV_SCHEMA_ROOT or (base_root / "response_examples")
        )
        self.rapidapi_key = rapidapi_key or ToolConfig.RAPIDAPI_KEY

    def list_categories(self) -> List[str]:
        if not self.tools_root.exists():
            return []
        return sorted([p.name for p in self.tools_root.iterdir() if p.is_dir()])

    def list_tools(self, category: str) -> List[str]:
        category_dir = self._category_dir(category)
        if not category_dir.exists():
            return []
        return sorted([p.stem for p in category_dir.glob("*.json")])

    def list_apis(self, category: str, tool_name: str) -> List[str]:
        meta = self.load_tool_metadata(category, tool_name)
        if not meta:
            return []
        return [item.get("name") for item in meta.get("api_list", []) if item.get("name")]

    def load_tool_metadata(self, category: str, tool_name: str) -> Optional[Dict[str, Any]]:
        category_dir = self._category_dir(category)
        tool_base = self._tool_base_name(category, tool_name)
        meta_path = category_dir / f"{tool_base}.json"
        if not meta_path.exists():
            return None
        with meta_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def run_tool(self, request: ToolRequest) -> Dict[str, Any]:
        tool_input = self._parse_tool_input(request.tool_input)
        if tool_input is None:
            return {"error": "Tool input parse error...", "response": ""}

        api_name = change_name(standardize(request.api_name))
        try:
            module = self._load_tool_module(request.category, request.tool_name)
        except FileNotFoundError as exc:
            return {"error": str(exc), "response": ""}
        except Exception as exc:
            return {"error": f"Failed to load tool module: {exc}", "response": ""}

        func = getattr(module, api_name, None)
        if func is None:
            return {"error": f"API函数不存在: {api_name}", "response": ""}

        if "toolbench_rapidapi_key" not in tool_input and self.rapidapi_key:
            tool_input["toolbench_rapidapi_key"] = self.rapidapi_key

        success, switch_flag, response_dict, save_cache = self._run_api(func, tool_input)
        observation = self._observation_shorten(
            response_dict,
            request.category,
            request.tool_name,
            api_name,
            request.strip,
        )
        result = str(observation)[:2048]
        return {
            "error": response_dict.get("error", ""),
            "response": result,
            "success": success,
            "switch": switch_flag,
            "save_cache": save_cache,
        }

    def _category_dir(self, category: str) -> Path:
        standard_category = self._standardize_category(category)
        return self.tools_root / standard_category

    def _tool_base_name(self, category: str, tool_name: str) -> str:
        standard_category = self._standardize_category(category)
        if tool_name.endswith(f"_for_{standard_category}"):
            tool_name = tool_name.replace(f"_for_{standard_category}", "")
        return standardize(tool_name)

    def _standardize_category(self, category: str) -> str:
        standard_category = category.replace(" ", "_").replace(",", "_").replace("/", "_")
        while " " in standard_category or "," in standard_category:
            standard_category = standard_category.replace(" ", "_").replace(",", "_")
        return standard_category.replace("__", "_")

    def _resolve_path(self, value: Union[str, Path]) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path.resolve()

        repo_root = Path(__file__).resolve().parents[3]
        candidate = repo_root / path
        if candidate.exists():
            return candidate.resolve()

        backend_root = Path(__file__).resolve().parents[2]
        return (backend_root / path).resolve()

    def _load_tool_module(self, category: str, tool_name: str):
        category_dir = self._category_dir(category)
        tool_base = self._tool_base_name(category, tool_name)
        module_path = category_dir / tool_base / "api.py"
        if not module_path.exists():
            raise FileNotFoundError(f"工具模块不存在: {module_path}")
        module_name = f"toolenv_{standardize(category)}_{tool_base}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载模块: {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _parse_tool_input(self, tool_input: Union[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if isinstance(tool_input, dict):
            return dict(tool_input)
        if not tool_input:
            return {}
        try:
            return json.loads(tool_input)
        except Exception:
            return None

    def _run_api(self, func, tool_input: Dict[str, Any]) -> Tuple[bool, bool, Dict[str, Any], bool]:
        success_flag = False
        switch_flag = False
        save_cache = False
        try:
            raw_response = func(**tool_input)
            response_dict, save_cache, switch_flag = process_error(raw_response)
            success_flag = True
        except Exception as exc:
            response_dict = {"error": f"Function executing error...\n{exc}", "response": ""}
            save_cache = False
        return success_flag, switch_flag, response_dict, save_cache

    def _observation_shorten(
        self,
        response_dict: Dict[str, Any],
        category: str,
        tool_name: str,
        api_name: str,
        strip_method: str,
    ) -> str:
        if strip_method not in {"filter", "random"}:
            return str(response_dict.get("response", ""))
        if strip_method == "random" and random.random() <= 0.5:
            return str(response_dict.get("response", ""))

        if not isinstance(response_dict.get("response"), dict):
            return str(response_dict.get("response", ""))

        schema = self._load_response_schema(category, tool_name, api_name)
        if schema:
            response_dict["response"] = dict_shorten(response_dict["response"], schema)
        return str(response_dict.get("response", ""))

    def _load_response_schema(
        self, category: str, tool_name: str, api_name: str
    ) -> Optional[Dict[str, Any]]:
        schema = None
        category_dir = self.schema_root / self._standardize_category(category)
        tool_base = self._tool_base_name(category, tool_name)
        schema_path = category_dir / f"{tool_base}.json"
        if schema_path.exists():
            with schema_path.open("r", encoding="utf-8") as f:
                schema_dicts = json.load(f)
            schema = self._extract_schema(schema_dicts.get("api_list", []), api_name)

        if schema:
            return schema

        meta = self.load_tool_metadata(category, tool_name)
        if not meta:
            return None
        return self._extract_schema(meta.get("api_list", []), api_name)

    def _extract_schema(self, api_list: List[Dict[str, Any]], api_name: str) -> Optional[Dict[str, Any]]:
        for schema_dict in api_list:
            schema_api_name = change_name(standardize(schema_dict.get("name", "")))
            if schema_api_name == api_name and schema_dict.get("schema"):
                return schema_dict["schema"]
        return None


def process_error(response: Any) -> Tuple[Dict[str, Any], bool, bool]:
    save_cache_flag = False
    switch_flag = False
    response_text = str(response)
    if "The request to the API has timed out. Please try again later" in response_text:
        return_dict = {"error": "API temporarily not working error...", "response": response}
    elif "Your Client (working) ---> Gateway (working) ---> API (not working)" in response_text:
        return_dict = {"error": "API not working error...", "response": response}
    elif "Unauthorized" in response_text or "unauthorized" in response_text:
        save_cache_flag = True
        return_dict = {"error": "Unauthorized error...", "response": response}
    elif "You are not subscribed to this API." in response_text:
        switch_flag = True
        return_dict = {"error": "Unsubscribed error...", "response": response}
    elif "Too many requests" in response_text:
        switch_flag = True
        return_dict = {"error": "Too many requests error...", "response": response}
    elif "You have exceeded" in response_text or "you are being rate limited" in response_text:
        switch_flag = True
        return_dict = {"error": "Rate limit error...", "response": response}
    elif "Access restricted. Check credits balance or enter the correct API key." in response_text:
        switch_flag = True
        return_dict = {"error": "Rate limit error...", "response": response}
    elif "Oops, an error in the gateway has occurred." in response_text:
        switch_flag = True
        return_dict = {"error": "Gateway error...", "response": response}
    elif "Blocked User. Please contact your API provider." in response_text:
        switch_flag = True
        return_dict = {"error": "Blocked error...", "response": response}
    elif "error" in response_text.lower():
        return_dict = {"error": "Message error...", "response": response}
    else:
        save_cache_flag = True
        return_dict = {"error": "", "response": response}
    return return_dict, save_cache_flag, switch_flag


def dict_shorten(origin: dict, schema: dict):
    for key, value in list(origin.items()):
        if key not in schema:
            del origin[key]
        else:
            if isinstance(value, dict):
                dict_shorten(value, schema[key])
            elif isinstance(value, list):
                if value and isinstance(value[0], dict):
                    for item in value:
                        dict_shorten(item, schema[key][0])
    return origin


def get_rapidapi_response(
    input_dict: dict,
    api_customization: bool = False,
    tools_root: Optional[str] = None,
    schema_root: Optional[str] = None,
):
    request = ToolRequest(
        category=input_dict["category"],
        tool_name=input_dict["tool_name"],
        api_name=input_dict["api_name"],
        tool_input=input_dict.get("tool_input", {}),
        strip=input_dict.get("strip", "none"),
    )
    server = ToolServer(
        tools_root=tools_root,
        schema_root=schema_root,
        rapidapi_key=input_dict.get("rapidapi_key"),
    )
    if api_customization and isinstance(request.tool_input, dict):
        request.tool_input.pop("toolbench_rapidapi_key", None)
    return server.run_tool(request)