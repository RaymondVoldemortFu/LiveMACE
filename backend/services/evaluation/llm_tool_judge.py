import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tiktoken
except Exception:  # pragma: no cover - optional dependency fallback
    tiktoken = None

from services.evaluation.base import BaseEvaluator
from services.evaluation.judge_output_utils import (
    extract_json_object,
    is_likely_truncated_json,
    normalize_judge_parsed,
)
from services.agent.llm_client import LLMClient

try:
    from services.agent.tool_selector import REQUIRED_TOOL_NAMES as ROUTING_REQUIRED_TOOL_NAMES
except Exception:
    ROUTING_REQUIRED_TOOL_NAMES = [
        "get_market_snapshot",
        "get_kline_history",
        "get_account_state",
        "get_history_decisions",
        "execute_trade",
    ]

_TOOL_QUALITY_SCORE_CACHE: Optional[Dict[str, int]] = None


def _safe_json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _load_tool_quality_scores() -> Dict[str, int]:
    global _TOOL_QUALITY_SCORE_CACHE
    if _TOOL_QUALITY_SCORE_CACHE is not None:
        return _TOOL_QUALITY_SCORE_CACHE

    default_path = (
        Path(__file__).resolve().parent
        / "tool_eval"
        / "tool_quality_scores_20260407_203219.json"
    )
    score_path = Path(
        (os.getenv("TOOL_QUALITY_SCORE_PATH") or str(default_path)).strip()
    ).expanduser()

    try:
        payload = json.loads(score_path.read_text(encoding="utf-8"))
        raw_scores = payload.get("scores") if isinstance(payload, dict) else {}
        if isinstance(raw_scores, dict):
            _TOOL_QUALITY_SCORE_CACHE = {
                str(k): int(v)
                for k, v in raw_scores.items()
                if isinstance(v, (int, float, str)) and str(v).strip() != ""
            }
        else:
            _TOOL_QUALITY_SCORE_CACHE = {}
    except Exception:
        _TOOL_QUALITY_SCORE_CACHE = {}
    return _TOOL_QUALITY_SCORE_CACHE


def compute_routing_quality_from_steps(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    required = set(ROUTING_REQUIRED_TOOL_NAMES)
    quality_scores = _load_tool_quality_scores()
    call_scores: List[float] = []
    selection_calls = 0
    scored_calls = 0

    for step in steps:
        if step.get("role") != "tool":
            continue
        payload = _safe_json_loads(step.get("tool_output") or step.get("content"))
        if not isinstance(payload, dict):
            continue
        selected_tools = payload.get("selected_tools")
        if not isinstance(selected_tools, list):
            continue

        # This tool output is from a select_tools routing call.
        selection_calls += 1
        tool_count = len(selected_tools)
        denom_count = tool_count - len(required)
        if denom_count <= 0:
            continue

        non_required = [str(t) for t in selected_tools if str(t) not in required]
        numerator = sum(max(0, min(4, int(quality_scores.get(t, 0)))) for t in non_required)
        denominator = denom_count * 4
        if denominator <= 0:
            continue
        call_scores.append(numerator / denominator)
        scored_calls += 1

    trace_score = (sum(call_scores) / len(call_scores)) if call_scores else 0.0
    return {
        "score": float(trace_score),
        "selection_calls": selection_calls,
        "scored_calls": scored_calls,
    }


def _build_judge_system_prompt(base_prompt: str) -> str:
    return (
        f"{base_prompt}\n\n"
        "Output constraints:\n"
        "- Respond with exactly one JSON object.\n"
        '- Include these numeric keys: "Tool Relevance Score", "Tool Timing / Budgeting Score", '
        '"Information Coverage Score", "Synthesis / Faithfulness Score".\n'
        '- Add a top-level "reason" field (string, <= 60 words).\n'
        "- Do not output markdown, code fences, comments, or any text before/after JSON."
    )


def _build_judge_messages(system_prompt: str, account_info: Dict[str, Any], steps: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    payload_trace = []
    for s in steps:
        payload_trace.append(
            {
                "step_number": s.get("step_number"),
                "role": s.get("role"),
                "content": s.get("content"),
                "tool_calls": s.get("tool_calls"),
                "tool_output": _safe_json_loads(s.get("tool_output") or s.get("content")),
            }
        )

    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": json.dumps(
                {"account": account_info, "trace": payload_trace},
                ensure_ascii=False,
            ),
        },
    ]


class LLMToolJudgeEvaluator(BaseEvaluator):
    def __init__(self, llm: LLMClient, system_prompt: str):
        self.llm = llm
        self.system_prompt = system_prompt

    @property
    def name(self) -> str:
        return "tool_use_llm_judge"

    def evaluate(self, agent_data: Dict[str, Any], market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        agent_data expects:
        - trace: a single trace dict with steps
        - account_info: dict with agent_type/model/name
        """
        trace = agent_data.get("trace") or {}
        steps = trace.get("steps", [])
        account_info = agent_data.get("account_info") or {}
        trace_id = str(trace.get("trace_id") or "unknown")
        account_name = str(account_info.get("account_name") or "unknown")
        context_label = f"trace_id={trace_id} account={account_name} model={self.llm.model}"

        system_prompt = _build_judge_system_prompt(self.system_prompt)
        messages = _build_judge_messages(system_prompt, account_info, steps)
        content = ""
        parsed: Dict[str, Any] = {}

        content = self._call_judge_model(
            messages=messages,
            force_json_object=True,
            temperature=0.1,
            context_label=context_label,
        )
        # Disabled fallback downgrade for performance/debug determinism.
        # try:
        #     content = self._call_judge_model(
        #         messages=messages,
        #         force_json_object=True,
        #         temperature=0.1,
        #     )
        # except Exception:
        #     content = self._call_judge_model(
        #         messages=messages,
        #         force_json_object=False,
        #         temperature=0.1,
        #     )

        parsed_json = extract_json_object(content)
        # Disabled parse-repair retry for performance/debug determinism.
        # if parsed_json is None and (is_likely_truncated_json(content) or content.strip()):
        #     repair_messages = messages + [
        #         {"role": "assistant", "content": content},
        #         {
        #             "role": "user",
        #             "content": (
        #                 "Your previous response was invalid or truncated. "
        #                 "Return exactly one complete JSON object now with the required keys. "
        #                 "No markdown and no extra text."
        #             ),
        #         },
        #     ]
        #     retry_content = self._call_judge_model(
        #         messages=repair_messages,
        #         force_json_object=True,
        #         temperature=0.0,
        #     )
        #     if retry_content:
        #         content = retry_content
        #         parsed_json = extract_json_object(content)

        if isinstance(parsed_json, dict):
            parsed = normalize_judge_parsed(parsed_json, content)
        else:
            parsed = normalize_judge_parsed({}, content)
            parsed["reason"] = "failed_to_parse_json"
        prompt_tokens = _count_message_tokens(messages, self.llm.model)
        completion_tokens = _count_text_tokens(content, self.llm.model)
        routing_quality = compute_routing_quality_from_steps(steps)
        return {
            "judge_raw": content,
            "judge_parsed": parsed,
            "routing_quality": routing_quality,
            "token_usage": {
                "model": self.llm.model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "method": "tiktoken_simple",
            },
        }

    def _call_judge_model(
        self,
        messages: List[Dict[str, str]],
        force_json_object: bool,
        temperature: float,
        context_label: str = "",
    ) -> str:
        timeout_sec_raw = (os.getenv("EVAL_LLM_TIMEOUT_SEC") or "").strip()
        try:
            timeout_sec = float(timeout_sec_raw) if timeout_sec_raw else 90.0
        except Exception:
            timeout_sec = 90.0
        retry_raw = (os.getenv("EVAL_LLM_TIMEOUT_RETRIES") or "").strip()
        try:
            timeout_retries = int(retry_raw) if retry_raw else 2
        except Exception:
            timeout_retries = 2
        timeout_retries = max(0, timeout_retries)

        kwargs: Dict[str, Any] = {
            "model": self.llm.model,
            "messages": messages,
            "tools": None,
            "temperature": temperature,
            "timeout": timeout_sec,
        }
        if force_json_object:
            kwargs["response_format"] = {"type": "json_object"}

        attempt = 0
        while True:
            try:
                response = self.llm.client.chat.completions.create(**kwargs)
                return response.choices[0].message.content or ""
            except Exception as e:
                is_timeout = self._is_timeout_error(e)
                if is_timeout and attempt < timeout_retries:
                    attempt += 1
                    print(
                        "[tool-eval][judge-timeout-retry] "
                        f"{context_label} force_json_object={force_json_object} "
                        f"timeout_sec={timeout_sec} retry={attempt}/{timeout_retries} "
                        f"error_type={type(e).__name__} error={e}"
                    )
                    time.sleep(min(1.0, 0.2 * (2 ** attempt)))
                    continue
                print(
                    "[tool-eval][judge-error] "
                    f"{context_label} force_json_object={force_json_object} "
                    f"timeout_sec={timeout_sec} retries={timeout_retries} "
                    f"error_type={type(e).__name__} error={e}"
                )
                raise

    @staticmethod
    def _is_timeout_error(error: Exception) -> bool:
        name = type(error).__name__.lower()
        message = str(error).lower()
        timeout_markers = (
            "timeout",
            "timed out",
            "readtimeout",
            "connecttimeout",
            "apitimeouterror",
            "timeouterror",
        )
        if any(marker in name for marker in timeout_markers):
            return True
        return any(marker in message for marker in timeout_markers)


def _get_encoder(model: str):
    if tiktoken is None:
        return None
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def _count_text_tokens(text: str, model: str) -> int:
    if not text:
        return 0
    enc = _get_encoder(model)
    if enc is None:
        # Fallback approximation: roughly 4 chars per token.
        return max(1, len(text) // 4)
    return len(enc.encode(text))


def _count_message_tokens(messages: List[Dict[str, Any]], model: str) -> int:
    enc = _get_encoder(model)
    if enc is None:
        joined = "".join(
            f"{msg.get('role', '')}{msg.get('name', '')}{msg.get('content', '') or ''}"
            for msg in messages
        )
        return max(1, len(joined) // 4) if joined else 0
    total = 0
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        name = msg.get("name", "") or ""
        total += len(enc.encode(role))
        total += len(enc.encode(content))
        if name:
            total += len(enc.encode(name))
    return total
