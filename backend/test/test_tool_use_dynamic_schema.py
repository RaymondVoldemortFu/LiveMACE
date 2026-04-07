import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.evaluation.tool_use_evaluator import ToolUseMetricsEvaluator


def test_select_tools_from_sampled_trace_is_not_marked_hallucinated():
    """
    DB sampled tool call example (agent_traces):
    {"function":{"name":"select_tools","arguments":"{\"task\":\"Get current account state...\"}"}}
    """
    traces = [
        {
            "trace_id": "sample-trace-select-tools",
            "steps": [
                {
                    "step_number": 1,
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_sample",
                            "function": {
                                "name": "select_tools",
                                "arguments": "{\"task\":\"Get current account state and recent trading history\"}",
                            },
                            "type": "function",
                        }
                    ],
                }
            ],
        }
    ]

    evaluator = ToolUseMetricsEvaluator()
    result = evaluator.evaluate(
        {
            "traces": traces,
            # Intentionally omit select_tools from static schemas to reproduce dynamic path.
            "tool_schemas": {
                "execute_trade": {
                    "type": "object",
                    "properties": {"operation": {"type": "string"}},
                    "required": ["operation"],
                }
            },
            "tool_schema_resolver": lambda _name: None,
        },
        {},
    )
    summary = result["summary"]
    assert summary["total_tool_calls"] == 1
    assert summary["dynamic_schema_hits"] == 1
    assert summary["dynamic_schema_misses"] == 0
    assert summary["hallucinated_calls"] == 0
    assert summary["invalid_param_calls"] == 0


def test_select_tools_without_task_reports_invalid_params_not_hallucination():
    traces = [
        {
            "trace_id": "sample-trace-select-tools-invalid-params",
            "steps": [
                {
                    "step_number": 1,
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_sample_invalid",
                            "function": {
                                "name": "select_tools",
                                "arguments": "{}",
                            },
                            "type": "function",
                        }
                    ],
                }
            ],
        }
    ]

    evaluator = ToolUseMetricsEvaluator()
    result = evaluator.evaluate(
        {
            "traces": traces,
            "tool_schemas": {},
            "tool_schema_resolver": lambda _name: None,
        },
        {},
    )
    summary = result["summary"]
    assert summary["dynamic_schema_hits"] == 1
    assert summary["hallucinated_calls"] == 0
    assert summary["invalid_param_calls"] == 1
