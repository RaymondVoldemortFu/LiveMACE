import json
import os
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List

from database.connection import get_db
from services.evaluation.data_loader import EvaluationDataLoader
from services.evaluation.tool_use_evaluator import ToolUseMetricsEvaluator, descriptive_stats
from services.evaluation.llm_tool_judge import LLMToolJudgeEvaluator
from services.agent.llm_client import LLMClient
from services.agent.env_wrapper import register_default_tools
from services.agent.public_apis_registry import register_public_api_tools
from services.agent.history_tool import HistoryTool
from services.agent.tools import ToolRegistry

_THREAD_LOCAL = threading.local()


def _load_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _build_registry(db, account_id: int) -> ToolRegistry:
    registry = ToolRegistry()
    register_default_tools(registry, db, account_id)
    try:
        register_public_api_tools(registry)
    except Exception:
        pass
    registry.register(HistoryTool(db, account_id))
    return registry


def _load_public_tools_schema() -> Dict[str, Dict[str, Any]]:
    schema_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "agent",
        "public-apis",
        "tools_schema.json",
    )
    schema_path = os.path.normpath(schema_path)
    if not os.path.isfile(schema_path):
        return {}
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            tools = json.load(f)
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for entry in tools or []:
        if not isinstance(entry, dict):
            continue
        func = entry.get("function") or {}
        name = func.get("name")
        if name:
            out[name] = func.get("parameters", {}) or {}
    return out


def _build_full_tool_schemas(db, account_id: int) -> Dict[str, Dict[str, Any]]:
    registry = _build_registry(db, account_id)
    schemas = {
        t["function"]["name"]: t["function"].get("parameters", {})
        for t in registry.openai_tools_all
    }
    public_schemas = _load_public_tools_schema()
    schemas.update(public_schemas)
    return schemas


class DynamicToolSchemaResolver:
    def __init__(self, db, account_id: int):
        self.db = db
        self.account_id = account_id
        self.registry: ToolRegistry = _build_registry(db, account_id)
        self.cache: Dict[str, Any] = {}

    def resolve(self, tool_name: str) -> Any:
        if not tool_name:
            return None
        if tool_name in self.cache:
            return self.cache[tool_name]

        schema = self._try_get_schema(tool_name)
        if schema is None:
            # Dynamic load fallback: rebuild registry once and retry.
            try:
                self.registry = _build_registry(self.db, self.account_id)
            except Exception:
                pass
            schema = self._try_get_schema(tool_name)

        self.cache[tool_name] = schema
        return schema

    def _try_get_schema(self, tool_name: str) -> Any:
        try:
            tool = self.registry.get(tool_name)
            return tool.parameters or {}
        except Exception:
            return None


def _group_traces(traces: List[Any]) -> Dict[str, List[Any]]:
    grouped = defaultdict(list)
    for t in traces:
        grouped[t.trace_id].append(t)
    return grouped


def _trace_to_steps(trace_rows: List[Any]) -> List[Dict[str, Any]]:
    rows = sorted(trace_rows, key=lambda r: r.step_number)
    steps = []
    for r in rows:
        tool_calls = None
        if r.tool_calls:
            try:
                tool_calls = json.loads(r.tool_calls)
            except Exception:
                tool_calls = r.tool_calls
        tool_output = None
        if r.tool_output:
            try:
                tool_output = json.loads(r.tool_output)
            except Exception:
                tool_output = r.tool_output
        steps.append(
            {
                "step_number": r.step_number,
                "role": r.role,
                "content": r.content,
                "tool_calls": tool_calls,
                "tool_output": tool_output,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return steps


def _parse_tool_name_from_call(call: Any) -> str:
    if not isinstance(call, dict):
        return ""
    function_block = call.get("function") if isinstance(call.get("function"), dict) else call
    name = function_block.get("name")
    return name if isinstance(name, str) else ""


def _extract_trace_tool_names(steps: List[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen = set()
    for step in steps:
        tool_calls = step.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            name = _parse_tool_name_from_call(call)
            if not name or name in seen:
                continue
            names.append(name)
            seen.add(name)
    return names


def _resolve_trace_dynamic_schemas(
    steps: List[Dict[str, Any]],
    base_tool_schemas: Dict[str, Dict[str, Any]],
    resolver: DynamicToolSchemaResolver,
) -> Dict[str, Dict[str, Any]]:
    dynamic_schemas: Dict[str, Dict[str, Any]] = {}
    for name in _extract_trace_tool_names(steps):
        if name in base_tool_schemas:
            continue
        schema = resolver.resolve(name)
        if isinstance(schema, dict):
            dynamic_schemas[name] = schema
    return dynamic_schemas


def _resolve_max_workers(total_tasks: int) -> int:
    raw = os.getenv("EVAL_MAX_WORKERS", "").strip()
    default_workers = min(8, (os.cpu_count() or 4))
    if raw:
        try:
            configured = int(raw)
            if configured > 0:
                default_workers = configured
        except Exception:
            pass
    # For high-cost tasks, keep upper bound to avoid API overload/spikes.
    capped = min(default_workers, 16)
    return max(1, min(capped, max(total_tasks, 1)))


def _get_thread_local_evaluators(
    judge_model: str,
    judge_api_key: str,
    judge_base_url: str,
    judge_prompt: str,
):
    if not hasattr(_THREAD_LOCAL, "objective_eval"):
        _THREAD_LOCAL.objective_eval = ToolUseMetricsEvaluator()
        _THREAD_LOCAL.judge_key = None
        _THREAD_LOCAL.llm_judge = None

    judge_key = (
        judge_model or "",
        judge_api_key or "",
        judge_base_url or "",
        judge_prompt,
    )
    if _THREAD_LOCAL.judge_key != judge_key or _THREAD_LOCAL.llm_judge is None:
        judge_llm = LLMClient(model=judge_model, api_key=judge_api_key, base_url=judge_base_url)
        _THREAD_LOCAL.llm_judge = LLMToolJudgeEvaluator(judge_llm, judge_prompt)
        _THREAD_LOCAL.judge_key = judge_key

    return _THREAD_LOCAL.objective_eval, _THREAD_LOCAL.llm_judge


def _evaluate_trace_job(
    trace_id: str,
    steps: List[Dict[str, Any]],
    account_info: Dict[str, Any],
    base_tool_schemas: Dict[str, Dict[str, Any]],
    trace_dynamic_schemas: Dict[str, Dict[str, Any]],
    judge_model: str,
    judge_api_key: str,
    judge_base_url: str,
    judge_prompt: str,
) -> Dict[str, Any]:
    trace_dict = {"trace_id": trace_id, "steps": steps}

    objective_eval, llm_judge = _get_thread_local_evaluators(
        judge_model=judge_model,
        judge_api_key=judge_api_key,
        judge_base_url=judge_base_url,
        judge_prompt=judge_prompt,
    )

    def _local_schema_resolver(tool_name: str):
        return trace_dynamic_schemas.get(tool_name)

    objective_metrics = objective_eval.evaluate(
        {
            "traces": [trace_dict],
            "tool_schemas": base_tool_schemas,
            "tool_schema_resolver": _local_schema_resolver,
        },
        {},
    )
    judge_metrics = llm_judge.evaluate({"trace": trace_dict, "account_info": account_info}, {})

    return {
        "trace_id": trace_id,
        "account": account_info,
        "objective_metrics": objective_metrics,
        "judge_metrics": judge_metrics,
    }


def run():
    db = next(get_db())
    loader = EvaluationDataLoader(db)

    eval_accounts = loader.get_agent_accounts()
    eval_time = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(results_dir, exist_ok=True)
    output_path = os.path.join(results_dir, f"tool_use_eval_{eval_time}.json")

    judge_model = os.getenv("EVAL_LLM_MODEL", "gpt-4o-mini")
    judge_api_key = os.getenv("EVAL_LLM_API_KEY", os.getenv("API_KEY"))
    judge_base_url = os.getenv("EVAL_LLM_BASE_URL", os.getenv("BASE_URL"))
    judge_prompt_path = os.path.join(
        os.path.dirname(__file__), "sys_prompts_eval", "tool_use_judge.md"
    )
    judge_prompt = _load_prompt(judge_prompt_path)

    results = []
    grouped_scores = defaultdict(lambda: defaultdict(list))
    token_agg = defaultdict(lambda: defaultdict(list))

    for account in eval_accounts:
        traces = loader.get_traces(account.id)
        grouped = _group_traces(traces)
        tool_schemas = _build_full_tool_schemas(db, account.id)
        dynamic_schema_resolver = DynamicToolSchemaResolver(db, account.id)

        total_traces = len(grouped)
        processed = 0
        max_workers = _resolve_max_workers(total_traces)
        jobs = []
        for trace_id, rows in grouped.items():
            steps = _trace_to_steps(rows)
            account_info = {
                "account_id": account.id,
                "account_name": account.name,
                "agent_type": account.agent_type,
                "model": account.model,
            }
            trace_dynamic_schemas = _resolve_trace_dynamic_schemas(
                steps=steps,
                base_tool_schemas=tool_schemas,
                resolver=dynamic_schema_resolver,
            )
            jobs.append((trace_id, steps, account_info, trace_dynamic_schemas))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    _evaluate_trace_job,
                    trace_id,
                    steps,
                    account_info,
                    tool_schemas,
                    trace_dynamic_schemas,
                    judge_model,
                    judge_api_key,
                    judge_base_url,
                    judge_prompt,
                ): trace_id
                for trace_id, steps, account_info, trace_dynamic_schemas in jobs
            }

            for future in as_completed(future_map):
                trace_id = future_map[future]
                try:
                    item = future.result()
                except Exception as exc:
                    # Keep batch execution stable: record failed trace and continue.
                    item = {
                        "trace_id": trace_id,
                        "account": {
                            "account_id": account.id,
                            "account_name": account.name,
                            "agent_type": account.agent_type,
                            "model": account.model,
                        },
                        "objective_metrics": {
                            "summary": {
                                "total_tool_calls": 0,
                                "unique_tool_calls": 0,
                                "total_steps": 0,
                                "tool_calls_per_step": 0.0,
                                "hallucinated_calls": 0,
                                "invalid_param_calls": 0,
                                "error_or_empty_calls": 0,
                                "invalid_output_calls": 0,
                                "no_op_calls": 0,
                                "dynamic_schema_hits": 0,
                                "dynamic_schema_misses": 0,
                                "hallucination_rate": 0.0,
                                "invalid_or_noop_rate": 0.0,
                            },
                            "details": {"notes": [f"worker_exception: {type(exc).__name__}"]},
                        },
                        "judge_metrics": {
                            "judge_raw": "",
                            "judge_parsed": {
                                "Tool Relevance Score": 0.0,
                                "Tool Timing / Budgeting Score": 0.0,
                                "Information Coverage Score": 0.0,
                                "Synthesis / Faithfulness Score": 0.0,
                                "reason": f"worker_exception: {type(exc).__name__}",
                                "raw_response": "",
                            },
                            "token_usage": {
                                "model": judge_model,
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "total_tokens": 0,
                                "method": "fallback_zero_due_to_exception",
                            },
                        },
                    }

                objective_metrics = item.get("objective_metrics", {})
                judge_metrics = item.get("judge_metrics", {})
                account_info = item.get("account", {})

                key = (account.agent_type, account.model)
                grouped_scores[key]["Tool Relevance Score"].append(
                    judge_metrics.get("judge_parsed", {}).get("Tool Relevance Score", 0)
                )
                grouped_scores[key]["Tool Timing / Budgeting Score"].append(
                    judge_metrics.get("judge_parsed", {}).get("Tool Timing / Budgeting Score", 0)
                )
                grouped_scores[key]["Information Coverage Score"].append(
                    judge_metrics.get("judge_parsed", {}).get("Information Coverage Score", 0)
                )
                grouped_scores[key]["Synthesis / Faithfulness Score"].append(
                    judge_metrics.get("judge_parsed", {}).get("Synthesis / Faithfulness Score", 0)
                )
                grouped_scores[key]["Tool Hallucination Rate"].append(
                    objective_metrics.get("summary", {}).get("hallucination_rate", 0)
                )
                grouped_scores[key]["Invalid / No-op Call Rate"].append(
                    objective_metrics.get("summary", {}).get("invalid_or_noop_rate", 0)
                )
                grouped_scores[key]["Tool Cost / Budget Usage"].append(
                    objective_metrics.get("summary", {}).get("tool_calls_per_step", 0)
                )
                token_usage = judge_metrics.get("token_usage", {})
                token_agg[key]["prompt_tokens"].append(token_usage.get("prompt_tokens", 0))
                token_agg[key]["completion_tokens"].append(token_usage.get("completion_tokens", 0))
                token_agg[key]["total_tokens"].append(token_usage.get("total_tokens", 0))

                results.append(item)
                processed += 1
                _print_progress(account.name, processed, total_traces)

    summary = []
    for (agent_type, model), metrics in grouped_scores.items():
        summary_entry = {
            "agent_type": agent_type,
            "model": model,
        }
        for metric_name, values in metrics.items():
            summary_entry[metric_name] = descriptive_stats(values)
        token_stats = token_agg.get((agent_type, model), {})
        summary_entry["token_usage"] = {
            "prompt_tokens": descriptive_stats(token_stats.get("prompt_tokens", [])),
            "completion_tokens": descriptive_stats(token_stats.get("completion_tokens", [])),
            "total_tokens": descriptive_stats(token_stats.get("total_tokens", [])),
        }
        summary.append(summary_entry)

    payload = {
        "generated_at": eval_time,
        "results": results,
        "summary": summary,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\nWrote evaluation results to {output_path}")


def _print_progress(account_name: str, current: int, total: int):
    if total == 0:
        return
    width = 30
    ratio = min(max(current / total, 0.0), 1.0)
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    msg = f"[{account_name}] [{bar}] {current}/{total}"
    sys.stdout.write("\r" + msg)
    sys.stdout.flush()


if __name__ == "__main__":
    run()
