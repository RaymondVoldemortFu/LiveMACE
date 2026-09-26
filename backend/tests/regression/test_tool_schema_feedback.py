"""Tool schema feedback survives both Agent bridges without rejected values."""

import json
from types import SimpleNamespace

import pytest

from benchmark.builtin.agents._legacy_ports import (
    InvokerBackedToolRegistry,
    tool_error_payload,
)
from benchmark.builtin.agents.rule_aware import _PublicToolBridge
from benchmark.builtin.tools.sandbox import SandboxToolsProvider
from benchmark.contracts import ExtensionRef, SANDBOX_WRITE, ToolResult
from benchmark.tools import SynchronousToolInvoker, ToolRegistry


@pytest.fixture
def runtime():
    calls = []

    def write(account_id, path, content):
        calls.append(("write", path, content))
        return "Success"

    def read(account_id, path):
        calls.append(("read", path))
        return "test file"

    def execute(account_id, command):
        calls.append(("execute", command))
        return 0, "test output"

    sandbox = SimpleNamespace(write_file=write, read_file=read, execute_command=execute)
    registry = ToolRegistry()
    registry.register_builtin_provider(
        ExtensionRef("core.tools", "1.0.0"), SandboxToolsProvider(sandbox)
    )
    registry.freeze()
    invoker = SynchronousToolInvoker(
        registry,
        account_id=1,
        decision_round_id="test",
        trace_id="test",
        capabilities=frozenset({SANDBOX_WRITE}),
    )
    return invoker, calls


def bridge_for(kind, invoker):
    return (
        InvokerBackedToolRegistry(invoker)
        if kind == "legacy"
        else _PublicToolBridge(invoker, [])
    )


@pytest.mark.parametrize("kind", ["legacy", "rule_aware"])
@pytest.mark.parametrize(
    "name,bad,good,required",
    [
        (
            "execute_shell_command",
            {"cmd": "echo test"},
            {"command": "echo test"},
            "command",
        ),
        (
            "write_file",
            {"path": "/workspace/test.py", "content": "print(1)"},
            {"file_path": "/workspace/test.py", "content": "print(1)"},
            "file_path",
        ),
        (
            "read_file",
            {"path": "/workspace/test.py"},
            {"file_path": "/workspace/test.py"},
            "file_path",
        ),
        (
            "run_python_script",
            {"code": "print(1)"},
            {"script_content": "print(1)"},
            "script_content",
        ),
        (
            "run_python_script",
            {"script": "print(1)"},
            {"script_content": "print(1)"},
            "script_content",
        ),
    ],
)
def test_real_invoker_feedback_names_missing_field_then_accepts_correction(
    runtime, kind, name, bad, good, required
):
    invoker, calls = runtime
    bridge = bridge_for(kind, invoker)
    feedback = bridge.get(name)(**bad)
    assert feedback["error_code"] == "TOOL_INPUT_INVALID"
    assert feedback["retryable"] is False
    error = feedback["validation_errors"][0]
    assert error == {
        "path": "$",
        "validator": "required",
        "message": f"'{required}' is a required property",
    }
    assert calls == []
    corrected = bridge.get(name)(**good)
    assert "error" not in corrected
    if name == "execute_shell_command":
        assert corrected == [0, "test output"]
    elif name == "write_file":
        assert corrected == "Success"
    elif name == "read_file":
        assert corrected == "test file"
    assert calls


@pytest.mark.parametrize("kind", ["legacy", "rule_aware"])
def test_real_invoker_type_error_does_not_echo_sensitive_argument_values(runtime, kind):
    invoker, calls = runtime
    sentinel = "private-test-token-must-not-echo"
    arguments = {"script_content": {"password": sentinel}}
    raw = invoker.call("core.run_python_script", arguments)
    assert sentinel in raw.metadata["errors"][0]["message"]
    feedback = bridge_for(kind, invoker).get("run_python_script")(**arguments)
    assert sentinel not in json.dumps(feedback)
    assert "arguments" not in feedback
    assert feedback["validation_errors"] == [
        {
            "path": "script_content",
            "validator": "type",
            "message": "Value has the wrong type; follow the tool input schema.",
        }
    ]
    assert calls == []


def test_only_bounded_validation_details_are_exposed():
    result = ToolResult(
        ok=False,
        error_code="TOOL_INPUT_INVALID",
        metadata={
            "arguments": {"api_key": "private-test-token"},
            "errors": [
                {
                    "path": "x",
                    "validator": "enum",
                    "message": "private-test-token is invalid",
                }
            ]
            * 100,
        },
    )
    payload = tool_error_payload(result)
    assert len(payload["validation_errors"]) == 10
    assert "private-test-token" not in json.dumps(payload)


def test_business_failure_preserves_code_and_retryability_without_metadata():
    result = ToolResult(
        ok=False,
        error_code="PROVIDER_BUSY",
        error_message="Try later",
        retryable=True,
        metadata={"password": "private-test-token"},
    )
    assert tool_error_payload(result) == {
        "error": "Try later",
        "error_code": "PROVIDER_BUSY",
        "retryable": True,
    }


def test_rule_aware_trade_rejection_flags_are_preserved():
    invoker = SimpleNamespace(
        call=lambda *args: ToolResult(
            ok=False,
            error_code="TOOL_INPUT_INVALID",
            error_message="Invalid",
            metadata={
                "errors": [
                    {
                        "validator": "required",
                        "path": "",
                        "message": "'symbol' is a required property",
                    }
                ]
            },
        )
    )
    payload = _PublicToolBridge(invoker, []).get("execute_trade")()
    assert payload["executed"] is False
    assert payload["reject_code"] == "TOOL_INPUT_INVALID"
    assert (
        payload["validation_errors"][0]["message"] == "'symbol' is a required property"
    )
