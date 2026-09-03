"""Command-line interface for extension validate / test / list."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from benchmark.contracts import to_jsonable
from benchmark.extensions import (
    ExtensionSettings,
    ExtensionStatus,
    MANIFEST_FILENAME,
    load_manifest,
    load_extensions,
    validate_extension_directory,
)
from benchmark.testing import (
    AgentCase,
    ToolCase,
    assert_agent_contract,
    assert_prompt_contract,
    assert_tool_contract,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpha-arena")
    sub = parser.add_subparsers(dest="group", required=True)
    extension = sub.add_parser("extension", help="Inspect and test extension directories")
    commands = extension.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="Statically validate an extension directory")
    validate.add_argument("directory", type=Path)
    validate.add_argument("--json", action="store_true", dest="json_output")

    test = commands.add_parser("test", help="Load an extension and run SPI contract checks")
    test.add_argument("directory", type=Path)

    list_cmd = commands.add_parser(
        "list",
        help="List declared extension contents without starting the scheduler",
    )
    list_cmd.add_argument("directory", type=Path)
    list_cmd.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _list_payload(directory: Path) -> dict[str, object]:
    manifest = load_manifest(directory / MANIFEST_FILENAME)
    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "agents": [component.id for component in manifest.components.agents],
        "tools": [component.provider for component in manifest.components.tools],
        "prompts": [
            {"directory": component.directory, "index": component.index}
            for component in manifest.components.prompts
        ],
    }


def _run_contracts(directory: Path) -> None:
    settings = ExtensionSettings(extension_roots=(directory.resolve(),))
    result = load_extensions(settings)
    records = [record for record in result.records if record.source.value == "external"]
    if not records:
        raise SystemExit(f"no extension loaded from {directory}")
    record = records[0]
    if record.status != ExtensionStatus.LOADED:
        raise SystemExit(
            f"extension {record.id} status={record.status} errors={record.to_dict()['errors']}"
        )
    for descriptor in result.agents.list():
        registered = result.agents.get(descriptor.id, descriptor.version)
        assert_agent_contract(registered.factory, (AgentCase(name=descriptor.id),))
    if result.tools.list():
        class _LoadedProvider:
            def __init__(self, tools):
                self._tools = tools

            def list_tools(self):
                return self._tools

        tools = tuple(result.tools.get(spec.name).tool for spec in result.tools.list())
        cases = tuple(
            ToolCase(name=spec.name, tool_name=spec.name, arguments={})
            for spec in result.tools.list()
            if not spec.input_schema.get("required")
        )
        assert_tool_contract(_LoadedProvider(tools), cases)
    if result.prompts.list():
        class _PromptView:
            def __init__(self, registry):
                self._registry = registry

            def list_prompts(self):
                return self._registry.list()

            def render(self, prompt_id, variables):
                return self._registry.render(prompt_id, variables)

        assert_prompt_contract(_PromptView(result.prompts))


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.group != "extension":
        raise SystemExit(2)
    directory = arguments.directory
    if arguments.command == "validate":
        report = validate_extension_directory(directory)
        if arguments.json_output:
            print(json.dumps(to_jsonable(report), sort_keys=True, ensure_ascii=False))
        elif report.valid:
            print("Extension is valid.")
        else:
            print("Extension is invalid.")
            for issue in report.errors:
                print(f"ERROR {issue.path}: {issue.message}")
        return 0 if report.valid else 2
    if arguments.command == "list":
        payload = _list_payload(directory)
        if arguments.json_output:
            print(json.dumps(payload, sort_keys=True, ensure_ascii=False))
        else:
            print(f"{payload['id']} {payload['version']}")
            for agent_id in payload["agents"]:
                print(f"agent {agent_id}")
            for provider in payload["tools"]:
                print(f"tool {provider}")
            for prompt in payload["prompts"]:
                print(f"prompt {prompt['directory']}")
        return 0
    if arguments.command == "test":
        _run_contracts(directory)
        print("Extension contract checks passed.")
        return 0
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())
