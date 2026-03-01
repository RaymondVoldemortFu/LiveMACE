import json
from typing import Any, Dict, List, Optional

import tiktoken

from services.evaluation.base import BaseEvaluator
from services.agent.llm_client import LLMClient


def _safe_json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


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

        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "account": account_info,
                        "trace": [
                            {
                                "step_number": s.get("step_number"),
                                "role": s.get("role"),
                                "content": s.get("content"),
                                "tool_calls": s.get("tool_calls"),
                                "tool_output": _safe_json_loads(s.get("tool_output") or s.get("content")),
                            }
                            for s in steps
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = self.llm.call(messages, tools=None)
        content = response.content or ""
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = {"raw_response": content}
        prompt_tokens = _count_message_tokens(messages, self.llm.model)
        completion_tokens = _count_text_tokens(content, self.llm.model)
        return {
            "judge_raw": content,
            "judge_parsed": parsed,
            "token_usage": {
                "model": self.llm.model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "method": "tiktoken_simple",
            },
        }


def _get_encoder(model: str):
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def _count_text_tokens(text: str, model: str) -> int:
    if not text:
        return 0
    enc = _get_encoder(model)
    return len(enc.encode(text))


def _count_message_tokens(messages: List[Dict[str, Any]], model: str) -> int:
    enc = _get_encoder(model)
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
