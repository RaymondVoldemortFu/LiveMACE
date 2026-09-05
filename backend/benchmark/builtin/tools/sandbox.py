"""Built-in sandbox shell, file, and Python Tools."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from benchmark.contracts import SANDBOX_WRITE, JsonValue, SideEffect, ToolContext
from benchmark.builtin.tools._support import (
    BoundCallableTool,
    LazyContainerSandbox,
    spec,
)

_SHELL_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "Shell command to execute"}
    },
    "required": ["command"],
}

_READ_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string", "description": "Absolute file path"}
    },
    "required": ["file_path"],
}

_WRITE_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string", "description": "Absolute file path"},
        "content": {"type": "string", "description": "Content to write"},
    },
    "required": ["file_path", "content"],
}

_PYTHON_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "script_content": {
            "type": "string",
            "description": "Python script content. Include print() statements to output analysis results.",
        }
    },
    "required": ["script_content"],
}

SHELL_SPEC = spec(
    "core.execute_shell_command",
    "Execute a shell command inside the sandboxed Linux environment. Returns (exit_code, output).",
    _SHELL_PARAMETERS,
    side_effect=SideEffect.SANDBOX_WRITE,
    capabilities=(SANDBOX_WRITE,),
    timeout_seconds=120.0,
)

READ_SPEC = spec(
    "core.read_file",
    "Read file content from the sandboxed environment. Output length is limited.",
    _READ_PARAMETERS,
    side_effect=SideEffect.SANDBOX_WRITE,
    capabilities=(SANDBOX_WRITE,),
)

WRITE_SPEC = spec(
    "core.write_file",
    "Write content to a file in the sandboxed environment. Missing files or directories will be created automatically.",
    _WRITE_PARAMETERS,
    side_effect=SideEffect.SANDBOX_WRITE,
    capabilities=(SANDBOX_WRITE,),
)

PYTHON_SPEC = spec(
    "core.run_python_script",
    (
        "Execute a Python script. Parameters must be a JSON object: "
        '{"script_content": "<python code>"}. '
        "script_content may include normal Python code, quotes, newlines, and indentation. "
        "The script will not automatically display the last expression value, so use print() to output results. "
        "For long scripts, prefer writing to a file and executing it."
    ),
    _PYTHON_PARAMETERS,
    side_effect=SideEffect.SANDBOX_WRITE,
    capabilities=(SANDBOX_WRITE,),
    timeout_seconds=120.0,
)


def _run_python(sandbox: Any, account_id: int, content: str) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"script_{timestamp}.py"
    filepath = f"/workspace/{filename}"
    if "\\n" in content and "\n" not in content:
        content = content.replace("\\n", "\n").replace("\\t", "\t")
    write_res = sandbox.write_file(account_id, filepath, content)
    if write_res != "Success":
        return {"error": f"Failed to write script: {write_res}"}
    exit_code, output = sandbox.execute_command(
        account_id, f"cd /workspace && python3 -u {filename}"
    )
    return {
        "exit_code": exit_code,
        "output": output if str(output).strip() else "(No output captured. Did you forget to print() the result?)",
    }


class SandboxToolsProvider:
    def __init__(self, sandbox: Any | None = None) -> None:
        self._sandbox = sandbox or LazyContainerSandbox()
        self._tools = (
            BoundCallableTool(SHELL_SPEC, self._execute_shell),
            BoundCallableTool(READ_SPEC, self._read_file),
            BoundCallableTool(WRITE_SPEC, self._write_file),
            BoundCallableTool(PYTHON_SPEC, self._run_python),
        )

    def list_tools(self) -> tuple[BoundCallableTool, ...]:
        return self._tools

    def _execute_shell(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> Any:
        return self._sandbox.execute_command(context.account_id, str(arguments["command"]))

    def _read_file(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> Any:
        return self._sandbox.read_file(context.account_id, str(arguments["file_path"]))

    def _write_file(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> Any:
        return self._sandbox.write_file(
            context.account_id,
            str(arguments["file_path"]),
            str(arguments["content"]),
        )

    def _run_python(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> Any:
        return _run_python(self._sandbox, context.account_id, str(arguments["script_content"]))


__all__ = ["SandboxToolsProvider"]
