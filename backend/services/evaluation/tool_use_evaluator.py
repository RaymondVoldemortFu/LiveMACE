import json
import math
import re
import statistics
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.evaluation.base import BaseEvaluator


BUILTIN_DYNAMIC_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    # Sampled from DB traces: routing-enabled agents emit this meta tool call.
    "select_tools": {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
        },
        "required": ["task"],
    }
}


def _safe_json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _parse_tool_call(call: Any) -> Tuple[Optional[str], Any]:
    if not isinstance(call, dict):
        return None, None
    function_block = call.get("function") if isinstance(call.get("function"), dict) else call
    name = function_block.get("name")
    args_raw = function_block.get("arguments")
    return name, args_raw


def _is_empty_result(result: Any) -> bool:
    if result is None:
        return True
    if isinstance(result, str):
        return len(result.strip()) == 0
    if isinstance(result, dict):
        if "error" in result:
            return True
        if "status" in result and result.get("status") == "error":
            return True
        if len(result) == 0:
            return True
    if isinstance(result, list):
        return len(result) == 0
    return False


def _validate_params(args: Any, schema: Dict[str, Any]) -> bool:
    if not isinstance(args, dict):
        return False
    required = schema.get("required", []) or []
    for key in required:
        if key not in args:
            return False
    properties = schema.get("properties") or {}
    for key, prop in properties.items():
        if key not in args:
            continue
        expected = prop.get("type")
        value = args.get(key)
        if expected == "string" and not isinstance(value, str):
            return False
        if expected == "integer" and not isinstance(value, int):
            return False
        if expected == "number" and not isinstance(value, (int, float)):
            return False
        if expected == "boolean" and not isinstance(value, bool):
            return False
        if expected == "object" and not isinstance(value, dict):
            return False
        if expected == "array" and not isinstance(value, list):
            return False
    return True


def _validate_tool_output(output: Any) -> bool:
    # Tool output can be text or structured JSON; treat explicit errors/empties as invalid.
    if _is_empty_result(output):
        return False
    if isinstance(output, (dict, list, str, int, float, bool)):
        return True
    return False


class ToolUseMetricsEvaluator(BaseEvaluator):
    @property
    def name(self) -> str:
        return "tool_use_objective_metrics"

    def evaluate(self, agent_data: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        agent_data expects:
        - traces: list of trace dicts with steps
        - tool_schemas: dict tool_name -> parameters schema (optional)
        - tool_schema_resolver: callable(tool_name) -> parameters schema (optional)
        """
        traces: List[Dict[str, Any]] = agent_data.get("traces", [])
        tool_schemas: Dict[str, Any] = agent_data.get("tool_schemas", {})
        tool_schema_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = agent_data.get(
            "tool_schema_resolver"
        )

        total_tool_calls = 0
        hallucinated_calls = 0
        invalid_params_calls = 0
        noop_calls = 0
        error_calls = 0
        invalid_output_calls = 0
        unique_tool_calls = 0
        total_steps = 0
        dynamic_schema_hits = 0
        dynamic_schema_misses = 0

        resolved_schema_cache: Dict[str, Optional[Dict[str, Any]]] = {}

        for trace in traces:
            seen_calls = set()
            for step in trace.get("steps", []):
                # Step definition for budget metric:
                # one LLM assistant output (with zero or more tool calls) counts as one step.
                # Tool result rows should not increase step count.
                if step.get("role") == "assistant":
                    total_steps += 1
                tool_calls = step.get("tool_calls") or []
                if not tool_calls:
                    continue
                for call in tool_calls:
                    total_tool_calls += 1
                    name, args_raw = _parse_tool_call(call)
                    args = _safe_json_loads(args_raw)

                    schema = None
                    if name:
                        schema = tool_schemas.get(name)
                        if schema is None and name in BUILTIN_DYNAMIC_TOOL_SCHEMAS:
                            schema = BUILTIN_DYNAMIC_TOOL_SCHEMAS[name]
                            dynamic_schema_hits += 1
                        if schema is None and tool_schema_resolver:
                            if name not in resolved_schema_cache:
                                resolved_schema_cache[name] = tool_schema_resolver(name)
                                if resolved_schema_cache[name] is None:
                                    dynamic_schema_misses += 1
                                else:
                                    dynamic_schema_hits += 1
                            schema = resolved_schema_cache.get(name)

                    if not name or schema is None:
                        hallucinated_calls += 1
                    else:
                        if not _validate_params(args, schema or {}):
                            invalid_params_calls += 1

                    try:
                        args_key = json.dumps(args, sort_keys=True, ensure_ascii=False)
                    except Exception:
                        args_key = str(args)
                    cache_key = f"{name}:{args_key}"
                    if cache_key in seen_calls:
                        noop_calls += 1
                    else:
                        seen_calls.add(cache_key)
                        unique_tool_calls += 1

            for step in trace.get("steps", []):
                if step.get("role") == "tool":
                    result = _safe_json_loads(step.get("tool_output") or step.get("content"))
                    if _is_empty_result(result):
                        error_calls += 1
                    elif not _validate_tool_output(result):
                        invalid_output_calls += 1

        hallucination_rate = (hallucinated_calls / total_tool_calls) if total_tool_calls else 0.0
        invalid_rate = (
            (error_calls + invalid_output_calls + invalid_params_calls + noop_calls) / total_tool_calls
        ) if total_tool_calls else 0.0
        tool_calls_per_step = (total_tool_calls / total_steps) if total_steps else 0.0

        return {
            "summary": {
                "total_tool_calls": total_tool_calls,
                "unique_tool_calls": unique_tool_calls,
                "total_steps": total_steps,
                "tool_calls_per_step": tool_calls_per_step,
                "hallucinated_calls": hallucinated_calls,
                "invalid_param_calls": invalid_params_calls,
                "error_or_empty_calls": error_calls,
                "invalid_output_calls": invalid_output_calls,
                "no_op_calls": noop_calls,
                "dynamic_schema_hits": dynamic_schema_hits,
                "dynamic_schema_misses": dynamic_schema_misses,
                "hallucination_rate": hallucination_rate,
                "invalid_or_noop_rate": invalid_rate,
            },
            "details": {
                "notes": [
                    "invalid_or_noop_rate includes error/empty outputs, invalid outputs, invalid params, and repeated identical calls",
                    "schema validation first checks preloaded schemas, then dynamically resolves tools found in llm trace",
                    "tool_calls_per_step uses assistant messages as steps (one assistant output + multiple tool returns = one step)",
                ]
            },
        }


def descriptive_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "variance": 0.0}

    numeric_values: List[float] = []
    for value in values:
        numeric = _coerce_to_float(value)
        if numeric is None:
            continue
        if not math.isfinite(numeric):
            continue
        numeric_values.append(numeric)

    if not numeric_values:
        return {"mean": 0.0, "median": 0.0, "variance": 0.0}

    mean_val = statistics.mean(numeric_values)
    median_val = statistics.median(numeric_values)
    variance_val = statistics.pvariance(numeric_values)
    return {
        "mean": float(mean_val),
        "median": float(median_val),
        "variance": float(variance_val),
    }


def _coerce_to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except Exception:
            pass
        # e.g. "8/10" -> 8.0
        ratio_match = re.match(r"^\s*([-+]?\d+(?:\.\d+)?)\s*/\s*10(?:\.0+)?\s*$", text)
        if ratio_match:
            return float(ratio_match.group(1))
    return None
