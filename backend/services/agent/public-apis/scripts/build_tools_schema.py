#!/usr/bin/env python3
"""Strict tools_schema.json generator with inferred parameter schemas."""

from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


ROOT = Path(__file__).resolve().parent.parent
APIS_DIR = ROOT / "apis"
UNSUPPORTED_APIS_PATH = ROOT / "unsupported_apis.py"
OUT_PATH = ROOT / "tools_schema.json"


def _load_unsupported() -> Set[str]:
    if not UNSUPPORTED_APIS_PATH.is_file():
        raise FileNotFoundError(f"Missing file: {UNSUPPORTED_APIS_PATH}")
    namespace: Dict[str, Any] = {}
    exec(UNSUPPORTED_APIS_PATH.read_text(encoding="utf-8"), namespace)
    unsupported = namespace.get("UNSUPPORTED_APIS")
    if not isinstance(unsupported, list) or not all(isinstance(x, str) for x in unsupported):
        raise ValueError("unsupported_apis.py must define UNSUPPORTED_APIS as list[str]")
    return set(unsupported)


def _discover_api_names(unsupported: Set[str]) -> List[str]:
    if not APIS_DIR.is_dir():
        raise FileNotFoundError(f"Missing directory: {APIS_DIR}")
    names: List[str] = []
    for entry in sorted(APIS_DIR.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if entry.name in unsupported:
            continue
        api_py = entry / "api.py"
        openapi_json = entry / "openapi.json"
        if not api_py.is_file():
            raise FileNotFoundError(f"Missing api.py for API '{entry.name}': {api_py}")
        if not openapi_json.is_file():
            raise FileNotFoundError(f"Missing openapi.json for API '{entry.name}': {openapi_json}")
        names.append(entry.name)
    return names


def _load_openapi(api_name: str) -> dict:
    path = APIS_DIR / api_name / "openapi.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"openapi.json for {api_name} must be an object")
    return data


def _extract_description(openapi: dict, api_name: str) -> str:
    info = openapi.get("info")
    if not isinstance(info, dict):
        raise ValueError(f"openapi.json for {api_name} missing info object")
    description = info.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"openapi.json for {api_name} missing non-empty info.description")
    return description.strip()


@dataclass
class ParamMeta:
    name: str
    defaults: List[Any] = field(default_factory=list)
    required: bool = False
    inferred_types: Set[str] = field(default_factory=set)
    enum_values: Set[Any] = field(default_factory=set)
    aliases: Set[str] = field(default_factory=set)

    def add_default(self, value: Any) -> None:
        if value is not None:
            self.defaults.append(value)

    def best_default(self) -> Any:
        if not self.defaults:
            return None
        # Prefer literal defaults from params.get(key, default) with primitive types.
        for value in self.defaults:
            if isinstance(value, (bool, int, float, str)):
                return value
        return self.defaults[-1]

    def to_schema(self) -> dict:
        schema: Dict[str, Any] = {}
        if self.inferred_types:
            # OpenAI tool schemas support JSON Schema-like type fields.
            if len(self.inferred_types) == 1:
                schema["type"] = next(iter(self.inferred_types))
            else:
                schema["type"] = sorted(self.inferred_types)
        if self.enum_values:
            schema["enum"] = sorted(self.enum_values, key=lambda x: str(x))
        default = self.best_default()
        if default is not None:
            schema["default"] = default
        description_parts = []
        if self.aliases:
            description_parts.append(f"Aliases: {', '.join(sorted(self.aliases))}.")
        if not description_parts:
            description_parts.append("Inferred from API implementation.")
        schema["description"] = " ".join(description_parts)
        return schema


class ParamInferer:
    def __init__(self, api_name: str, source: str) -> None:
        self.api_name = api_name
        self.source = source
        try:
            self.tree = ast.parse(source)
        except SyntaxError as exc:
            raise SyntaxError(f"Failed to parse {api_name}/api.py: {exc}") from exc

        self.functions: Dict[str, ast.FunctionDef] = {
            node.name: node for node in self.tree.body if isinstance(node, ast.FunctionDef)
        }
        if "run" not in self.functions:
            raise ValueError(f"{api_name}/api.py must define run(params)")
        self.param_meta: Dict[str, ParamMeta] = {}
        self._visited: Set[Tuple[str, Tuple[str, ...]]] = set()

    def infer(self) -> dict:
        run_fn = self.functions["run"]
        if not run_fn.args.args:
            raise ValueError(f"{self.api_name}/api.py run() must have at least one parameter")
        run_param_name = run_fn.args.args[0].arg
        self._analyze_function(run_fn.name, {run_param_name}, {}, {})
        self._infer_required_from_error_messages()
        self._infer_enums_from_error_messages()
        required = sorted([name for name, meta in self.param_meta.items() if meta.required])
        properties = {name: self.param_meta[name].to_schema() for name in sorted(self.param_meta)}
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    def _ensure_param(self, name: str) -> ParamMeta:
        if name not in self.param_meta:
            self.param_meta[name] = ParamMeta(name=name)
        return self.param_meta[name]

    def _analyze_function(
        self,
        fn_name: str,
        param_var_names: Set[str],
        var_to_keys: Dict[str, Set[str]],
        loop_values: Dict[str, List[str]],
    ) -> None:
        state_key = (fn_name, tuple(sorted(param_var_names)))
        if state_key in self._visited:
            return
        self._visited.add(state_key)
        fn = self.functions.get(fn_name)
        if fn is None:
            raise ValueError(f"{self.api_name}/api.py references missing helper function '{fn_name}'")
        self._analyze_statements(fn.body, param_var_names, var_to_keys, loop_values)

    def _analyze_statements(
        self,
        statements: List[ast.stmt],
        param_var_names: Set[str],
        var_to_keys: Dict[str, Set[str]],
        loop_values: Dict[str, List[str]],
    ) -> None:
        for stmt in statements:
            self._analyze_statement(stmt, param_var_names, var_to_keys, loop_values)

    def _analyze_statement(
        self,
        stmt: ast.stmt,
        param_var_names: Set[str],
        var_to_keys: Dict[str, Set[str]],
        loop_values: Dict[str, List[str]],
    ) -> None:
        if isinstance(stmt, ast.Assign):
            keys, default = self._extract_get_chain(stmt.value, param_var_names, loop_values)
            if keys:
                for key in keys:
                    meta = self._ensure_param(key)
                    meta.add_default(default)
                    alias_set = set(keys) - {key}
                    meta.aliases.update(alias_set)
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        var_to_keys[target.id] = set(keys)
                return

            if isinstance(stmt.value, ast.Name) and stmt.value.id in param_var_names:
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        param_var_names.add(target.id)
                return

        if isinstance(stmt, ast.For):
            loop_var = stmt.target.id if isinstance(stmt.target, ast.Name) else None
            iter_values = self._extract_string_iterable(stmt.iter)
            next_loop_values = dict(loop_values)
            if loop_var and iter_values:
                next_loop_values[loop_var] = iter_values
            self._analyze_statements(stmt.body, set(param_var_names), dict(var_to_keys), next_loop_values)
            self._analyze_statements(stmt.orelse, set(param_var_names), dict(var_to_keys), next_loop_values)
            return

        if isinstance(stmt, ast.If):
            self._infer_types_from_condition(stmt.test, var_to_keys)
            self._infer_enums_from_condition(stmt.test, var_to_keys)
            self._analyze_statements(stmt.body, set(param_var_names), dict(var_to_keys), dict(loop_values))
            self._analyze_statements(stmt.orelse, set(param_var_names), dict(var_to_keys), dict(loop_values))
            return

        if isinstance(stmt, ast.Try):
            self._analyze_statements(stmt.body, set(param_var_names), dict(var_to_keys), dict(loop_values))
            self._analyze_statements(stmt.orelse, set(param_var_names), dict(var_to_keys), dict(loop_values))
            self._analyze_statements(stmt.finalbody, set(param_var_names), dict(var_to_keys), dict(loop_values))
            for handler in stmt.handlers:
                self._analyze_statements(handler.body, set(param_var_names), dict(var_to_keys), dict(loop_values))
            return

        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                self._infer_types_from_call(node, var_to_keys)
                self._analyze_helper_call(node, param_var_names, var_to_keys)

    def _extract_get_chain(
        self, expr: ast.expr, param_var_names: Set[str], loop_values: Dict[str, List[str]]
    ) -> Tuple[List[str], Any]:
        keys: List[str] = []
        default: Any = None

        def walk(node: ast.AST) -> None:
            nonlocal default
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
                for value in node.values:
                    walk(value)
                return
            call_keys, call_default = self._extract_get_call(node, param_var_names, loop_values)
            if call_keys:
                keys.extend(call_keys)
                if call_default is not None:
                    default = call_default
                return
            literal = self._literal_or_none(node)
            if literal is not None:
                default = literal
            for child in ast.iter_child_nodes(node):
                walk(child)

        walk(expr)
        unique_keys = []
        seen = set()
        for key in keys:
            if key not in seen:
                unique_keys.append(key)
                seen.add(key)
        return unique_keys, default

    def _extract_get_call(
        self, node: ast.expr, param_var_names: Set[str], loop_values: Dict[str, List[str]]
    ) -> Tuple[List[str], Any]:
        if not isinstance(node, ast.Call):
            return [], None
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "get":
            return [], None
        if not isinstance(node.func.value, ast.Name) or node.func.value.id not in param_var_names:
            return [], None

        keys: List[str] = []
        if node.args:
            key_arg = node.args[0]
            if isinstance(key_arg, ast.Constant) and isinstance(key_arg.value, str):
                keys = [key_arg.value]
            elif isinstance(key_arg, ast.Name) and key_arg.id in loop_values:
                keys = list(loop_values[key_arg.id])
            else:
                raise ValueError(
                    f"{self.api_name}/api.py contains params.get with non-literal key; cannot infer safely"
                )
        default = None
        if len(node.args) >= 2:
            default = self._literal_or_none(node.args[1])
        return keys, default

    def _infer_types_from_call(self, node: ast.Call, var_to_keys: Dict[str, Set[str]]) -> None:
        if isinstance(node.func, ast.Name):
            fn = node.func.id
            if fn in {"int", "float", "str", "bool"} and node.args and isinstance(node.args[0], ast.Name):
                var_name = node.args[0].id
                keys = var_to_keys.get(var_name, set())
                inferred = {
                    "int": "integer",
                    "float": "number",
                    "str": "string",
                    "bool": "boolean",
                }[fn]
                for key in keys:
                    self._ensure_param(key).inferred_types.add(inferred)
            if fn == "_parse_bool" and node.args and isinstance(node.args[0], ast.Name):
                var_name = node.args[0].id
                for key in var_to_keys.get(var_name, set()):
                    self._ensure_param(key).inferred_types.add("boolean")

    def _infer_types_from_condition(self, test: ast.expr, var_to_keys: Dict[str, Set[str]]) -> None:
        for node in ast.walk(test):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance":
                if len(node.args) < 2 or not isinstance(node.args[0], ast.Name):
                    continue
                var_name = node.args[0].id
                keys = var_to_keys.get(var_name, set())
                if not keys:
                    continue
                type_names = self._extract_type_names(node.args[1])
                mapping = {
                    "str": "string",
                    "int": "integer",
                    "float": "number",
                    "bool": "boolean",
                    "list": "array",
                    "dict": "object",
                }
                for t in type_names:
                    if t in mapping:
                        for key in keys:
                            self._ensure_param(key).inferred_types.add(mapping[t])

    def _infer_enums_from_condition(self, test: ast.expr, var_to_keys: Dict[str, Set[str]]) -> None:
        for node in ast.walk(test):
            if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
                continue
            left = node.left
            right = node.comparators[0]
            values: Optional[List[Any]] = None
            variable: Optional[str] = None
            if isinstance(left, ast.Name) and isinstance(node.ops[0], (ast.In, ast.NotIn)):
                variable = left.id
                values = self._extract_literal_collection(right)
            elif isinstance(right, ast.Name) and isinstance(node.ops[0], (ast.In, ast.NotIn)):
                variable = right.id
                values = self._extract_literal_collection(left)
            if not variable or not values:
                continue
            for key in var_to_keys.get(variable, set()):
                meta = self._ensure_param(key)
                meta.enum_values.update(values)
                if not meta.inferred_types:
                    meta.inferred_types.add("string")

    def _analyze_helper_call(
        self, node: ast.Call, param_var_names: Set[str], var_to_keys: Dict[str, Set[str]]
    ) -> None:
        if not isinstance(node.func, ast.Name):
            return
        helper = self.functions.get(node.func.id)
        if helper is None:
            return
        helper_param_vars: Set[str] = set()
        for index, arg in enumerate(node.args):
            if isinstance(arg, ast.Name) and (arg.id in param_var_names or arg.id in var_to_keys):
                if index < len(helper.args.args):
                    helper_param_vars.add(helper.args.args[index].arg)
        if helper_param_vars:
            self._analyze_function(helper.name, helper_param_vars, {}, {})

    def _infer_required_from_error_messages(self) -> None:
        # Parse phrases like:
        # "Missing required parameter: text"
        # "Missing required parameters: lat, lon"
        for match in re.finditer(r"Missing required parameter(?:s)?\s*:\s*([^\"]+)", self.source, re.IGNORECASE):
            message_part = match.group(1)
            if " or " in message_part.lower():
                # "a or b" means at least one, not all simultaneously required.
                continue
            tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", message_part)
            token_set = set(tokens)
            for key, meta in self.param_meta.items():
                if key in token_set:
                    meta.required = True

    def _infer_enums_from_error_messages(self) -> None:
        # Parse phrases like:
        # "Invalid action. Allowed: encode, decode"
        for match in re.finditer(
            r"Invalid\s+([A-Za-z_][A-Za-z0-9_]*)\.[^\n\"]*Allowed:\s*([^\"]+)",
            self.source,
            re.IGNORECASE,
        ):
            param_name = match.group(1)
            raw_values = match.group(2)
            values = [v.strip().strip(".") for v in raw_values.split(",") if v.strip()]
            if not values:
                continue
            if param_name not in self.param_meta:
                continue
            meta = self.param_meta[param_name]
            meta.enum_values.update(values)
            if not meta.inferred_types:
                meta.inferred_types.add("string")

    @staticmethod
    def _literal_or_none(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
            if isinstance(node.operand.value, (int, float)):
                return -node.operand.value
        return None

    @staticmethod
    def _extract_type_names(node: ast.AST) -> List[str]:
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, ast.Tuple):
            out = []
            for elt in node.elts:
                if isinstance(elt, ast.Name):
                    out.append(elt.id)
            return out
        return []

    @staticmethod
    def _extract_literal_collection(node: ast.AST) -> Optional[List[Any]]:
        if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
            values = []
            for elt in node.elts:
                if isinstance(elt, ast.Constant):
                    values.append(elt.value)
                else:
                    return None
            return values
        return None

    @staticmethod
    def _extract_string_iterable(node: ast.AST) -> Optional[List[str]]:
        values = ParamInferer._extract_literal_collection(node)
        if values is None:
            return None
        if all(isinstance(v, str) for v in values):
            return values
        return None


def _build_tool_entry(api_name: str) -> dict:
    openapi = _load_openapi(api_name)
    description = _extract_description(openapi, api_name)
    source = (APIS_DIR / api_name / "api.py").read_text(encoding="utf-8")
    inferred_parameters = ParamInferer(api_name, source).infer()
    _normalize_schema_for_tool_validation(inferred_parameters)
    return {
        "type": "function",
        "function": {
            "name": api_name,
            "description": description,
            "parameters": inferred_parameters,
        },
    }


def _normalize_schema_for_tool_validation(schema: Dict[str, Any]) -> None:
    """
    Make inferred schemas compatible with strict tool validators:
    - array type must define `items`
    - object type should define `properties` or `additionalProperties`
    """

    def _type_includes(node_type: Any, expected: str) -> bool:
        if isinstance(node_type, str):
            return node_type == expected
        if isinstance(node_type, list):
            return expected in node_type
        return False

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            node_type = node.get("type")

            if _type_includes(node_type, "array") and "items" not in node:
                node["items"] = {}

            if (
                _type_includes(node_type, "object")
                and "properties" not in node
                and "additionalProperties" not in node
            ):
                node["additionalProperties"] = True

            properties = node.get("properties")
            if isinstance(properties, dict):
                for sub_schema in properties.values():
                    _walk(sub_schema)

            for key in ("items", "additionalProperties"):
                sub_schema = node.get(key)
                if isinstance(sub_schema, (dict, list)):
                    _walk(sub_schema)

            for key in ("anyOf", "oneOf", "allOf"):
                variants = node.get(key)
                if isinstance(variants, list):
                    for variant in variants:
                        _walk(variant)
        elif isinstance(node, list):
            for sub in node:
                _walk(sub)

    _walk(schema)


def main() -> None:
    unsupported = _load_unsupported()
    api_names = _discover_api_names(unsupported)
    tools = [_build_tool_entry(name) for name in api_names]
    OUT_PATH.write_text(json.dumps(tools, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(tools)} tools to {OUT_PATH}")


if __name__ == "__main__":
    main()
