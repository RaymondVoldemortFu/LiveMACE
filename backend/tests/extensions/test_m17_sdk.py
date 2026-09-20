from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import subprocess
import sys
import zipfile

import pytest

from benchmark.cli import main
from benchmark.contracts import (
    AgentRunResult,
    PromptSpec,
    SideEffect,
    TRADING_WRITE,
    TerminationReason,
    ToolResult,
    ToolSpec,
)
from benchmark.extensions import ExtensionSettings, ExtensionStatus, load_extensions
from benchmark.testing import (
    AgentCase,
    FakeTradeGateway,
    ToolCase,
    arguments_from_input_schema,
    assert_agent_contract,
    assert_prompt_contract,
    assert_tool_contract,
    build_fake_context,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
EXAMPLES_ROOT = REPO_ROOT / "examples" / "extensions"


@pytest.mark.parametrize(
    "name",
    ("minimal-agent", "read-only-tool", "prompt-override", "combined-extension"),
)
def test_m17_example_is_valid_and_catalog_loadable(name: str):
    root = EXAMPLES_ROOT / name
    result = load_extensions(ExtensionSettings(extension_roots=(root,)))
    assert result.records[0].status == ExtensionStatus.LOADED


def test_prompt_only_example_has_no_python_files():
    assert not list((EXAMPLES_ROOT / "prompt-override").rglob("*.py"))


def test_read_only_example_does_not_request_trading_write():
    result = load_extensions(
        ExtensionSettings(extension_roots=(EXAMPLES_ROOT / "read-only-tool",))
    )
    spec = result.tools.list()[0]
    assert spec.side_effect is SideEffect.READ_ONLY
    assert "trading.write" not in spec.required_capabilities


def test_contract_helpers_accept_a_public_synchronous_component():
    class Factory:
        def create(self, context, config):
            class Agent:
                def run(self, decision_context):
                    return AgentRunResult(
                        trace_id=decision_context.trace_id,
                        decision_round_id=decision_context.decision_round_id,
                        termination_reason=TerminationReason.HOLD,
                    )

            return Agent()

    assert_agent_contract(
        Factory(),
        [
            AgentCase(
                context=build_fake_context(account_id=8),
                expected_termination=TerminationReason.HOLD,
            )
        ],
    )

    class Tool:
        spec = ToolSpec(
            name="example.m17.echo",
            description="Echo a value",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
            output_schema={"type": "integer"},
            side_effect=SideEffect.READ_ONLY,
        )

        def invoke(self, context, arguments):
            return ToolResult(ok=True, value=arguments["value"])

    class Provider:
        def list_tools(self):
            return (Tool(),)

    assert_tool_contract(
        Provider(),
        [ToolCase("example.m17.echo", {"value": 3}, expected_value=3)],
    )


def test_arguments_from_input_schema_honors_string_and_minimum_constraints():
    assert arguments_from_input_schema(
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "value": {"type": "integer", "minimum": 10},
            },
            "required": ["name", "value"],
        }
    ) == {"name": "example", "value": 10}


def test_tool_contract_auto_probe_invokes_write_tools():
    calls = []

    class WriteTool:
        spec = ToolSpec(
            name="example.m17.write",
            description="Record a paper result",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect=SideEffect.MEMORY_WRITE,
            required_capabilities=("memory.write",),
        )

        def invoke(self, context, arguments):
            calls.append((context, arguments))
            return ToolResult(ok=True, value={})

    class Provider:
        def list_tools(self):
            return (WriteTool(),)

    assert_tool_contract(Provider())
    assert len(calls) == 1


def test_tool_contract_auto_probe_invokes_constrained_read_only_tools():
    calls = []

    class ConstrainedTool:
        spec = ToolSpec(
            name="example.m17.constrained",
            description="A constrained read-only Tool",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer", "minimum": 10}},
                "required": ["value"],
            },
            output_schema={"type": "integer"},
            side_effect=SideEffect.READ_ONLY,
        )

        def invoke(self, context, arguments):
            calls.append(arguments)
            return ToolResult(ok=True, value=arguments["value"])

    class Provider:
        def list_tools(self):
            return (ConstrainedTool(),)

    assert_tool_contract(Provider())
    assert calls == [{"value": 10}]


def test_tool_contract_auto_probe_requires_explicit_case_when_example_is_invalid():
    calls = []

    class PatternTool:
        spec = ToolSpec(
            name="example.m17.pattern",
            description="A pattern-constrained read-only Tool",
            input_schema={
                "type": "object",
                "properties": {"code": {"type": "string", "pattern": "^[0-9]+$"}},
                "required": ["code"],
            },
            output_schema={"type": "string"},
            side_effect=SideEffect.READ_ONLY,
        )

        def invoke(self, context, arguments):
            calls.append(arguments)
            return ToolResult(ok=True, value=arguments["code"])

    class Provider:
        def list_tools(self):
            return (PatternTool(),)

    with pytest.raises(AssertionError, match="explicit ToolCase"):
        assert_tool_contract(Provider())
    assert calls == []


def test_tool_contract_rejects_unauthorized_trading_write():
    class TradeTool:
        spec = ToolSpec(
            name="example.m17.trade",
            description="Place a paper order",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect=SideEffect.TRADING_WRITE,
            required_capabilities=(TRADING_WRITE,),
        )

        def invoke(self, context, arguments):
            return ToolResult(ok=True, value={})

    class Provider:
        def list_tools(self):
            return (TradeTool(),)

    with pytest.raises(AssertionError, match="trading.write"):
        assert_tool_contract(Provider())


def test_tool_contract_redacts_secrets_in_runtime_events():
    class SecretTool:
        spec = ToolSpec(
            name="example.m17.secret",
            description="Accept a credential-shaped field",
            input_schema={
                "type": "object",
                "properties": {
                    "password": {"type": "string"},
                    "value": {"type": "integer"},
                },
                "required": ["password", "value"],
            },
            output_schema={"type": "object"},
            side_effect=SideEffect.READ_ONLY,
        )

        def invoke(self, context, arguments):
            del context
            return ToolResult(ok=True, value={"echo": arguments["value"]})

    class Provider:
        def list_tools(self):
            return (SecretTool(),)

    assert_tool_contract(
        Provider(),
        [
            ToolCase(
                "example.m17.secret",
                {"password": "s3cret-token", "value": 1},
                expected_value={"echo": 1},
            )
        ],
    )


def test_agent_contract_rejects_awaitable_factory_and_run():
    async def _created():
        return None

    class AsyncFactory:
        def create(self, context, config):
            return _created()

    with pytest.raises(AssertionError, match="awaitable"):
        assert_agent_contract(AsyncFactory())

    async def _run(decision_context):
        return AgentRunResult(
            trace_id=decision_context.trace_id,
            decision_round_id=decision_context.decision_round_id,
            termination_reason=TerminationReason.HOLD,
        )

    class Factory:
        def create(self, context, config):
            class Agent:
                def run(self, decision_context):
                    return _run(decision_context)

            return Agent()

    with pytest.raises(AssertionError, match="awaitable"):
        assert_agent_contract(Factory())


def test_tool_and_prompt_contracts_reject_awaitables():
    async def _listed():
        return ()

    class AsyncListProvider:
        def list_tools(self):
            return _listed()

    with pytest.raises(AssertionError, match="awaitable"):
        assert_tool_contract(AsyncListProvider())

    async def _invoke(context, arguments):
        del context, arguments
        return ToolResult(ok=True, value=1)

    class AsyncInvokeTool:
        spec = ToolSpec(
            name="example.m17.async-invoke",
            description="Returns an awaitable",
            input_schema={"type": "object"},
            output_schema={"type": "integer"},
            side_effect=SideEffect.READ_ONLY,
        )

        def invoke(self, context, arguments):
            return _invoke(context, arguments)

    class InvokeProvider:
        def list_tools(self):
            return (AsyncInvokeTool(),)

    with pytest.raises(AssertionError, match="awaitable"):
        assert_tool_contract(InvokeProvider())

    spec = PromptSpec("com.example.m17.async-prompt", "1.0.0", ())

    class AsyncListPrompts:
        def list_prompts(self):
            async def _list():
                return (spec,)

            return _list()

        def render(self, prompt_id, variables):
            raise AssertionError("render should not be reached")

    with pytest.raises(AssertionError, match="awaitable"):
        assert_prompt_contract(AsyncListPrompts())

    class AsyncRender:
        def list_prompts(self):
            return (spec,)

        def render(self, prompt_id, variables):
            async def _render():
                return None

            return _render()

    with pytest.raises(AssertionError, match="awaitable"):
        assert_prompt_contract(AsyncRender())


def test_agent_contract_includes_original_exception_text():
    class Factory:
        def create(self, context, config):
            raise ValueError("missing api key")

    with pytest.raises(AssertionError, match="missing api key"):
        assert_agent_contract(Factory())


def test_agent_case_deadline_is_applied_to_default_build_context():
    deadline = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    captured = {}

    class Factory:
        def create(self, context, config):
            captured["deadline"] = getattr(context.tools, "_deadline_at", None)

            class Agent:
                def run(self, decision_context):
                    return AgentRunResult(
                        trace_id=decision_context.trace_id,
                        decision_round_id=decision_context.decision_round_id,
                        termination_reason=TerminationReason.HOLD,
                    )

            return Agent()

    assert_agent_contract(
        Factory(),
        [AgentCase(context=build_fake_context(account_id=3), deadline_at=deadline)],
    )
    assert captured["deadline"] == deadline


def test_prompt_contract_reports_invalid_specs_as_contract_failures():
    class Provider:
        def list_prompts(self):
            return (object(),)

        def render(self, prompt_id, variables):
            raise AssertionError("render should not be reached")

    with pytest.raises(AssertionError, match="not PromptSpec"):
        assert_prompt_contract(Provider())


def test_fake_trade_gateway_is_idempotent_and_persistence_free():
    from benchmark.contracts import Market, TradeCommand
    from decimal import Decimal

    gateway = FakeTradeGateway()
    command = TradeCommand(
        account_id=1,
        operation="open",
        market=Market.CRYPTO,
        symbol="BTC",
        direction="long",
        sizing_mode="quantity",
        sizing_value=Decimal("1"),
        leverage=1,
        reason="test",
        idempotency_key="m17-1",
    )
    first = gateway.execute(command)
    second = gateway.execute(command)
    assert first == second
    assert gateway.commands == [command, command]


def test_cli_validate_test_and_list_examples():
    command = [sys.executable, "-m", "benchmark.cli"]
    validated = subprocess.run(
        [
            *command,
            "extension",
            "validate",
            str(EXAMPLES_ROOT / "minimal-agent"),
            "--json",
        ],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert validated.returncode == 0
    assert '"valid": true' in validated.stdout

    tested = subprocess.run(
        [
            *command,
            "extension",
            "test",
            str(EXAMPLES_ROOT / "combined-extension"),
            "--json",
        ],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert tested.returncode == 0
    assert '"passed": true' in tested.stdout

    listed = subprocess.run(
        [
            *command,
            "extension",
            "list",
            str(EXAMPLES_ROOT / "prompt-override"),
            "--json",
        ],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0
    assert "com.example.prompt-override.system" in listed.stdout


def test_cli_test_applies_agent_schema_defaults(tmp_path: Path):
    root = tmp_path / "cli-defaults"
    root.mkdir()
    (root / "alpha-arena-extension.yaml").write_text(
        "api_version: 1\n"
        "id: com.example.cli-defaults\n"
        "version: 1.0.0\n"
        "name: CLI Defaults\n"
        "python:\n"
        '  requires: ">=3.10"\n'
        "  entrypoint: agent:Factory\n"
        "components:\n"
        "  agents:\n"
        "    - id: com.example.cli-defaults\n"
        "      factory: agent:Factory\n"
        "      config_schema: schema.json\n",
        encoding="utf-8",
    )
    (root / "schema.json").write_text(
        json.dumps(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "mode": {"type": "string", "default": "paper"},
                    "label": {"type": "string"},
                },
                "required": ["label"],
            }
        ),
        encoding="utf-8",
    )
    (root / "agent.py").write_text(
        "from benchmark.contracts import AgentRunResult, TerminationReason\n"
        "\n"
        "class Factory:\n"
        "    def create(self, context, config):\n"
        "        if config.get('mode') != 'paper':\n"
        "            raise AssertionError(f'expected default mode, got {config!r}')\n"
        "        if not isinstance(config.get('label'), str):\n"
        "            raise AssertionError(f'expected string label, got {config!r}')\n"
        "        class Agent:\n"
        "            def run(self, decision_context):\n"
        "                return AgentRunResult(\n"
        "                    trace_id=decision_context.trace_id,\n"
        "                    decision_round_id=decision_context.decision_round_id,\n"
        "                    termination_reason=TerminationReason.HOLD,\n"
        "                )\n"
        "        return Agent()\n",
        encoding="utf-8",
    )
    assert main(["extension", "test", str(root)]) == 0


@pytest.mark.slow
def test_wheel_contains_loadable_examples(tmp_path: Path):
    out = tmp_path / "dist"
    completed = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.fail(completed.stdout + completed.stderr)
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, completed.stderr
    install_root = tmp_path / "site"
    with zipfile.ZipFile(wheels[0]) as archive:
        archive.extractall(install_root)
        names = archive.namelist()
    examples = (
        "minimal-agent",
        "read-only-tool",
        "prompt-override",
        "combined-extension",
    )
    for name in examples:
        assert f"examples/extensions/{name}/alpha-arena-extension.yaml" in names
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path\n"
                "from benchmark.extensions import ExtensionSettings, ExtensionStatus, load_extensions\n"
                "roots = [\n"
                "    Path('examples/extensions') / name\n"
                "    for name in ("
                "'minimal-agent', 'read-only-tool', 'prompt-override', 'combined-extension'"
                ")\n"
                "]\n"
                "for root in roots:\n"
                "    result = load_extensions(ExtensionSettings(extension_roots=(root,)))\n"
                "    records = [item for item in result.records if item.source.value == 'external']\n"
                "    if not records or records[0].status != ExtensionStatus.LOADED:\n"
                "        raise SystemExit(root.as_posix())\n"
            ),
        ],
        cwd=install_root,
        env={**os.environ, "PYTHONPATH": str(install_root)},
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
