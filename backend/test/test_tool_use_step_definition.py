import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.evaluation.tool_use_evaluator import ToolUseMetricsEvaluator


def test_step_counts_assistant_outputs_not_tool_rows():
    traces = [
        {
            "trace_id": "step-def-trace",
            "steps": [
                {
                    "step_number": 1,
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "get_account_state",
                                "arguments": "{}",
                            }
                        },
                        {
                            "function": {
                                "name": "get_market_snapshot",
                                "arguments": "{\"symbol\":\"BTC\",\"market\":\"CRYPTO\"}",
                            }
                        },
                    ],
                },
                {"step_number": 2, "role": "tool", "content": "{\"status\":\"ok\"}"},
                {"step_number": 3, "role": "tool", "content": "{\"status\":\"ok\"}"},
            ],
        }
    ]

    evaluator = ToolUseMetricsEvaluator()
    result = evaluator.evaluate(
        {
            "traces": traces,
            "tool_schemas": {
                "get_account_state": {"type": "object", "properties": {}},
                "get_market_snapshot": {
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}, "market": {"type": "string"}},
                    "required": ["symbol", "market"],
                },
            },
        },
        {},
    )
    summary = result["summary"]
    assert summary["total_tool_calls"] == 2
    assert summary["total_steps"] == 1
    assert summary["tool_calls_per_step"] == 2.0
