import json
import statistics
from typing import Any, Dict, List, Optional

from services.evaluation.base import BaseEvaluator


def _safe_json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


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


class ToolUseMetricsEvaluator(BaseEvaluator):
    @property
    def name(self) -> str:
        return "tool_use_objective_metrics"

    def evaluate(self, agent_data: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        agent_data expects:
        - traces: list of trace dicts with steps
        - tool_schemas: dict tool_name -> parameters schema
        """
        traces: List[Dict[str, Any]] = agent_data.get("traces", [])
        tool_schemas: Dict[str, Any] = agent_data.get("tool_schemas", {})

        total_tool_calls = 0
        hallucinated_calls = 0
        invalid_params_calls = 0
        noop_calls = 0
        error_calls = 0
        unique_tool_calls = 0
        total_steps = 0

        for trace in traces:
            seen_calls = set()
            for step in trace.get("steps", []):
                total_steps += 1
                tool_calls = step.get("tool_calls") or []
                if not tool_calls:
                    continue
                for call in tool_calls:
                    total_tool_calls += 1
                    func = call.get("function") if isinstance(call, dict) else {}
                    name = func.get("name") if isinstance(func, dict) else None
                    args_raw = func.get("arguments") if isinstance(func, dict) else None
                    args = _safe_json_loads(args_raw)

                    if not name or name not in tool_schemas:
                        hallucinated_calls += 1
                    else:
                        schema = tool_schemas[name] or {}
                        if not _validate_params(args, schema):
                            invalid_params_calls += 1

                    cache_key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
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

        hallucination_rate = (hallucinated_calls / total_tool_calls) if total_tool_calls else 0.0
        invalid_rate = ((error_calls + invalid_params_calls + noop_calls) / total_tool_calls) if total_tool_calls else 0.0
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
                "no_op_calls": noop_calls,
                "hallucination_rate": hallucination_rate,
                "invalid_or_noop_rate": invalid_rate,
            },
            "details": {
                "notes": [
                    "invalid_or_noop_rate includes error/empty, invalid params, and repeated identical calls",
                    "tool_calls_per_step approximates budget usage due to missing latency/token data",
                ]
            },
        }


def descriptive_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "variance": 0.0}
    mean_val = statistics.mean(values)
    median_val = statistics.median(values)
    variance_val = statistics.pvariance(values)
    return {
        "mean": float(mean_val),
        "median": float(median_val),
        "variance": float(variance_val),
    }
