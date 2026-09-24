"""Pure redaction for Agent and Tool observations."""

import json
import re

from benchmark.contracts import to_jsonable
from benchmark.tools.invoker import redact_tool_value


def redact(value, secrets=()):
    value = redact_tool_value(to_jsonable(value))
    if isinstance(value, dict):
        return {k: redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(redact(parsed, secrets), ensure_ascii=False)
        value = re.sub(r"(?i)(bearer\s+)[\w.\-/+=]+", r"\1[REDACTED]", value)
        value = re.sub(
            r"(?i)((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            value,
        )
        return value
    return value
