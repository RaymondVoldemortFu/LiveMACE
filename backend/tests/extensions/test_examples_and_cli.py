"""M13/M17: example extensions, CLI, and catalog contribution."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys

from benchmark.cli import main
from benchmark.contracts import TRADING_WRITE
from benchmark.extensions import (
    ExtensionSettings,
    ExtensionStatus,
    build_extension_runtime,
    load_extensions,
    settings_from_environ,
    validate_extension_directory,
)
from benchmark.testing import (
    AgentCase,
    FakeTradeCommandGateway,
    ToolCase,
    assert_agent_contract,
    assert_prompt_contract,
    assert_tool_contract,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
EXAMPLES_ROOT = REPO_ROOT / "examples" / "extensions"
EXAMPLE_DIRS = (
    EXAMPLES_ROOT / "minimal-agent",
    EXAMPLES_ROOT / "read-only-tool",
    EXAMPLES_ROOT / "prompt-override",
    EXAMPLES_ROOT / "combined-extension",
)


def test_settings_from_environ_parses_colon_and_comma_lists(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    settings = settings_from_environ(
        {
            "ALPHA_ARENA_EXTENSION_DIRS": f"{first}:{second}:",
            "ALPHA_ARENA_DISABLED_EXTENSIONS": "com.example.one, com.example.two",
            "ALPHA_ARENA_ALLOWED_CAPABILITIES": "market.read,account.read",
        }
    )
    assert settings.extension_roots == (first, second)
    assert settings.disabled_extensions == frozenset(
        {"com.example.one", "com.example.two"}
    )
    assert settings.allowed_capabilities == frozenset({"market.read", "account.read"})
    assert settings.builtin_root is None


def test_settings_from_environ_keeps_all_capabilities_when_unset():
    from benchmark.contracts import KNOWN_CAPABILITIES

    settings = settings_from_environ({})
    assert settings.extension_roots == ()
    assert settings.disabled_extensions == frozenset()
    assert settings.allowed_capabilities == KNOWN_CAPABILITIES


def test_four_example_directories_validate_as_complete_extensions():
    for directory in EXAMPLE_DIRS:
        report = validate_extension_directory(directory)
        assert report.valid, (directory.name, [issue.message for issue in report.errors])


def test_prompt_override_ships_no_python():
    root = EXAMPLES_ROOT / "prompt-override"
    assert list(root.rglob("*.py")) == []


def test_example_python_imports_only_public_spi():
    allowed_roots = {
        "benchmark",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "json",
        "pathlib",
        "typing",
        "uuid",
    }
    forbidden = {"api", "database", "services", "fastapi", "sqlalchemy", "repositories"}
    for directory in EXAMPLE_DIRS:
        for path in directory.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    root = name.split(".", 1)[0]
                    assert root not in forbidden, f"{path}: {name}"
                    if root not in allowed_roots and not name.startswith("benchmark."):
                        raise AssertionError(f"{path} imported {name}")


def test_cli_validate_test_and_list_for_each_example():
    for directory in EXAMPLE_DIRS:
        assert main(["extension", "validate", str(directory)]) == 0
        assert main(["extension", "test", str(directory)]) == 0
        assert main(["extension", "list", str(directory)]) == 0


def test_cli_list_json_and_module_entrypoint(capsys):
    directory = EXAMPLES_ROOT / "minimal-agent"
    assert main(["extension", "list", str(directory), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "com.example.minimal-agent"
    assert payload["agents"] == ["com.example.minimal-agent"]

    completed = subprocess.run(
        [sys.executable, "-m", "benchmark", "extension", "validate", str(directory)],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Extension is valid." in completed.stdout


def test_read_only_tool_cannot_request_trading_write():
    result = load_extensions(
        ExtensionSettings(extension_roots=(EXAMPLES_ROOT / "read-only-tool",))
    )
    assert result.records[0].status == ExtensionStatus.LOADED
    spec = result.tools.list()[0]
    assert TRADING_WRITE not in spec.required_capabilities
    provider = type(
        "Loaded",
        (),
        {"list_tools": lambda self: (result.tools.get(spec.name).tool,)},
    )()
    assert_tool_contract(
        provider,
        (ToolCase(name="echo", tool_name=spec.name, arguments={"symbol": "BTC"}),),
    )


def test_combined_extension_contributes_agent_tool_and_prompt_with_builtin():
    runtime = build_extension_runtime(
        ExtensionSettings(extension_roots=(EXAMPLES_ROOT / "combined-extension",))
    )
    records = {record.id: record.status for record in runtime.catalog.list_extensions()}
    assert records["benchmark.core"] == ExtensionStatus.LOADED
    assert records["com.example.combined"] == ExtensionStatus.LOADED
    agent_ids = {descriptor.id for descriptor in runtime.catalog.list_agents()}
    assert {
        "core.react",
        "core.multi-agent",
        "core.advanced-multi-agent",
        "core.rule-aware",
        "com.example.combined.agent",
    }.issubset(agent_ids)
    tool_names = {spec.name for spec in runtime.catalog.list_tools()}
    assert "com.example.combined.status" in tool_names
    profile_ids = {profile.id for profile in runtime.catalog.list_prompt_profiles()}
    assert "com.example.combined.default" in profile_ids
    factory = runtime.agents.get("com.example.combined.agent").factory
    assert_agent_contract(factory, (AgentCase(name="combined"),))

    class _PromptView:
        def list_prompts(self):
            return runtime.prompts.list()

        def render(self, prompt_id, variables):
            return runtime.prompts.render(prompt_id, variables)

    assert_prompt_contract(_PromptView())


def test_invalid_external_extension_does_not_block_builtin(tmp_path):
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "alpha-arena-extension.yaml").write_text(
        "api_version: 1\n"
        "id: com.example.broken\n"
        "version: 1.0.0\n"
        "name: Broken\n"
        "components:\n"
        "  prompts:\n"
        "    - directory: missing\n"
        "      index: missing/index.yaml\n",
        encoding="utf-8",
    )
    runtime = build_extension_runtime(ExtensionSettings(extension_roots=(broken,)))
    by_id = {record.id: record for record in runtime.catalog.list_extensions()}
    assert by_id["benchmark.core"].status == ExtensionStatus.LOADED
    assert by_id["com.example.broken"].status != ExtensionStatus.LOADED
    assert [descriptor.id for descriptor in runtime.catalog.list_agents()] == [
        "core.advanced-multi-agent",
        "core.multi-agent",
        "core.react",
        "core.rule-aware",
    ]


def test_fake_trade_gateway_does_not_need_a_database():
    from benchmark.contracts import Market, TradeCommand

    gateway = FakeTradeCommandGateway()
    command = TradeCommand(
        account_id=1,
        operation="hold",
        market=Market.CRYPTO,
        symbol="",
        direction="long",
        sizing_mode=None,
        sizing_value=None,
        leverage=1,
        reason="test",
        idempotency_key="fake-1",
    )
    result = gateway.execute(command)
    assert result.accepted is True
    assert result.executed is False
    assert gateway.commands == [command]
