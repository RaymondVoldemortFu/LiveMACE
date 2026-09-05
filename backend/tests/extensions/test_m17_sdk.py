from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
import os

import pytest

from benchmark.contracts import (
    AgentRunResult,
    SideEffect,
    TerminationReason,
    ToolResult,
    ToolSpec,
)
from benchmark.extensions import ExtensionSettings, ExtensionStatus, load_extensions
from benchmark.testing import (
    AgentCase,
    FakeTradeGateway,
    ToolCase,
    assert_agent_contract,
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


def test_tool_contract_auto_probe_does_not_invoke_write_tools():
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
    assert calls == []


def test_tool_contract_skips_auto_probe_when_schema_example_is_invalid():
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
    assert calls == []


def test_prompt_contract_reports_invalid_specs_as_contract_failures():
    class Provider:
        def list_prompts(self):
            return (object(),)

        def render(self, prompt_id, variables):
            raise AssertionError("render should not be reached")

    with pytest.raises(AssertionError, match="not PromptSpec"):
        from benchmark.testing import assert_prompt_contract

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


@pytest.mark.slow
def test_wheel_contains_loadable_examples(tmp_path: Path):
    if (
        shutil.which("pyproject-build") is None
        and not (BACKEND_ROOT / ".venv/bin/pyproject-build").exists()
    ):
        pytest.skip("pyproject-build is not installed")
    try:
        import hatchling  # noqa: F401
    except ImportError:
        pytest.skip("hatchling is not installed in the test environment")

    out = tmp_path / "dist"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(out),
        ],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.fail(completed.stdout + completed.stderr)
    wheel = next(out.glob("*.whl"))
    install_root = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(install_root)
        names = archive.namelist()
    assert "examples/extensions/minimal-agent/alpha-arena-extension.yaml" in names
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path\n"
                "from benchmark.extensions import ExtensionSettings, ExtensionStatus, load_extensions\n"
                "root = Path('examples/extensions/minimal-agent')\n"
                "result = load_extensions(ExtensionSettings(extension_roots=(root,)))\n"
                "raise SystemExit(0 if result.records[0].status == ExtensionStatus.LOADED else 1)\n"
            ),
        ],
        cwd=install_root,
        env={**os.environ, "PYTHONPATH": str(install_root)},
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
