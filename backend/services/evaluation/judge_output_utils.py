import json
import math
import re
from typing import Any, Dict, Optional


def coerce_score(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        score = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        score = None
        try:
            score = float(text)
        except Exception:
            ratio_match = re.match(r"^\s*([-+]?\d+(?:\.\d+)?)\s*/\s*10(?:\.0+)?\s*$", text)
            if ratio_match:
                score = float(ratio_match.group(1))
        if score is None:
            return None
    else:
        return None

    if not math.isfinite(score):
        return None
    return max(0.0, min(10.0, score))


def normalize_judge_parsed(parsed: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
    score_keys = [
        "Tool Relevance Score",
        "Tool Timing / Budgeting Score",
        "Information Coverage Score",
        "Synthesis / Faithfulness Score",
    ]

    normalized: Dict[str, Any] = {}
    for key in score_keys:
        normalized[key] = coerce_score(parsed.get(key))
        if normalized[key] is None:
            normalized[key] = 0.0

    reason = parsed.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = parsed.get("rationale")
    if isinstance(reason, dict):
        try:
            reason = json.dumps(reason, ensure_ascii=False)
        except Exception:
            reason = str(reason)
    if not isinstance(reason, str) or not reason.strip():
        reason = "normalized_from_raw_response"
    normalized["reason"] = reason
    normalized["raw_response"] = raw_text
    return normalized


def is_likely_truncated_json(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith("{") and not stripped.endswith("}"):
        return True
    # common partial-output sign in logs
    if stripped.count("{") > stripped.count("}"):
        return True
    return False


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", stripped, re.IGNORECASE)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    start = stripped.find("{")
    while start != -1:
        candidate = _first_balanced_object(stripped[start:])
        if candidate:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        start = stripped.find("{", start + 1)
    return None


def _first_balanced_object(text: str) -> Optional[str]:
    depth = 0
    in_string = False
    escaped = False
    start = None
    for idx, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return text[start : idx + 1]
            if depth < 0:
                return None
    return None
