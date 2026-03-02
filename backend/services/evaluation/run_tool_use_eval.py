import json
import os
import sys
from collections import defaultdict
from datetime import datetime
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


def run():
    db = next(get_db())
    loader = EvaluationDataLoader(db)

    eval_accounts = loader.get_agent_accounts()
    eval_time = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
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
    judge_llm = LLMClient(model=judge_model, api_key=judge_api_key, base_url=judge_base_url)
    llm_judge = LLMToolJudgeEvaluator(judge_llm, judge_prompt)
    objective_eval = ToolUseMetricsEvaluator()

    results = []
    grouped_scores = defaultdict(lambda: defaultdict(list))
    token_agg = defaultdict(lambda: defaultdict(list))

    for account in eval_accounts:
        traces = loader.get_traces(account.id)
        grouped = _group_traces(traces)
        tool_schemas = _build_full_tool_schemas(db, account.id)

        total_traces = len(grouped)
        processed = 0
        for trace_id, rows in grouped.items():
            steps = _trace_to_steps(rows)
            trace_dict = {"trace_id": trace_id, "steps": steps}
            account_info = {
                "account_id": account.id,
                "account_name": account.name,
                "agent_type": account.agent_type,
                "model": account.model,
            }

            objective_metrics = objective_eval.evaluate(
                {"traces": [trace_dict], "tool_schemas": tool_schemas}, {}
            )
            judge_metrics = llm_judge.evaluate(
                {"trace": trace_dict, "account_info": account_info}, {}
            )

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
                objective_metrics["summary"].get("hallucination_rate", 0)
            )
            grouped_scores[key]["Invalid / No-op Call Rate"].append(
                objective_metrics["summary"].get("invalid_or_noop_rate", 0)
            )
            grouped_scores[key]["Tool Cost / Budget Usage"].append(
                objective_metrics["summary"].get("tool_calls_per_step", 0)
            )
            token_usage = judge_metrics.get("token_usage", {})
            token_agg[key]["prompt_tokens"].append(token_usage.get("prompt_tokens", 0))
            token_agg[key]["completion_tokens"].append(token_usage.get("completion_tokens", 0))
            token_agg[key]["total_tokens"].append(token_usage.get("total_tokens", 0))

            results.append(
                {
                    "trace_id": trace_id,
                    "account": account_info,
                    "objective_metrics": objective_metrics,
                    "judge_metrics": judge_metrics,
                }
            )
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
