import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.evaluation import llm_tool_judge


def test_compute_routing_quality_from_steps_formula():
    # Override cache for deterministic test values.
    llm_tool_judge._TOOL_QUALITY_SCORE_CACHE = {
        "chartgenerator": 4,
        "dictionary": 2,
        "didyoumean": 1,
    }

    required = set(llm_tool_judge.ROUTING_REQUIRED_TOOL_NAMES)
    selected = list(required) + ["chartgenerator", "dictionary", "didyoumean"]
    expected = (4 + 2 + 1) / ((len(selected) - len(required)) * 4)

    steps = [
        {
            "role": "tool",
            "content": json.dumps({"selected_tools": selected, "min_k": 15}, ensure_ascii=False),
        }
    ]
    out = llm_tool_judge.compute_routing_quality_from_steps(steps)
    assert out["selection_calls"] == 1
    assert out["scored_calls"] == 1
    assert abs(out["score"] - expected) < 1e-9


def test_compute_routing_quality_ignores_non_select_outputs():
    llm_tool_judge._TOOL_QUALITY_SCORE_CACHE = {"chartgenerator": 4}
    steps = [
        {"role": "tool", "content": json.dumps({"status": "ok"})},
        {"role": "assistant", "content": "no tools"},
    ]
    out = llm_tool_judge.compute_routing_quality_from_steps(steps)
    assert out["selection_calls"] == 0
    assert out["scored_calls"] == 0
    assert out["score"] == 0.0
