"""The lightweight ``alpha-arena`` command line interface.

Only extension commands live here.  They intentionally stop at validation,
catalog loading, and contract checks; no application scheduler or persistence
bootstrap is imported or started.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from benchmark.contracts import (
    KNOWN_CAPABILITIES,
    ExtensionManifestError,
    to_jsonable,
)
from benchmark.extensions import (
    ExtensionSettings,
    ExtensionStatus,
    MANIFEST_FILENAME,
    load_extensions,
    load_manifest,
    validate_extension_directory,
)
from benchmark.prompts import parse_prompt_directory
from benchmark.testing import (
    AgentCase,
    FakeEventSink,
    arguments_from_input_schema,
    assert_agent_contract,
    assert_prompt_contract,
    assert_tool_contract,
    build_fake_agent_build_context,
    build_fake_context,
)
from benchmark.tools import SynchronousToolInvoker


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpha-arena")
    commands = parser.add_subparsers(dest="command", required=True)
    extension = commands.add_parser(
        "extension", help="validate, test, or list an extension directory"
    )
    extension_commands = extension.add_subparsers(
        dest="extension_command", required=True
    )

    validate = extension_commands.add_parser("validate")
    validate.add_argument("directory", type=Path)
    validate.add_argument("--json", action="store_true", dest="json_output")

    test = extension_commands.add_parser("test")
    test.add_argument("directory", type=Path)
    test.add_argument("--json", action="store_true", dest="json_output")

    listing = extension_commands.add_parser("list")
    listing.add_argument("directory", type=Path, nargs="+")
    listing.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _issue_dict(issue: Any) -> dict[str, Any]:
    return {
        "path": getattr(issue, "path", ""),
        "message": getattr(issue, "message", str(issue)),
        "code": getattr(issue, "code", ""),
        "validator": getattr(issue, "validator", ""),
    }


def _print_payload(payload: Any, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(to_jsonable(payload), sort_keys=True, ensure_ascii=False))
        return
    if isinstance(payload, dict) and "valid" in payload:
        if payload["valid"]:
            print("Extension is valid.")
        else:
            print("Extension is invalid.")
        for issue in payload.get("errors", ()):
            code = f" [{issue['code']}]" if issue.get("code") else ""
            print(f"ERROR {issue.get('path') or 'manifest'}{code}: {issue['message']}")
        for issue in payload.get("warnings", ()):
            code = f" [{issue['code']}]" if issue.get("code") else ""
            print(
                f"WARNING {issue.get('path') or 'manifest'}{code}: {issue['message']}"
            )
        return
    if isinstance(payload, dict) and "passed" in payload:
        print(
            "Extension contract tests passed."
            if payload["passed"]
            else "Extension contract tests failed."
        )
        for error in payload.get("errors", ()):
            print(f"ERROR: {error}")
        return
    rows = payload if isinstance(payload, list) else [payload]
    for row in rows:
        if isinstance(row, dict):
            print(
                f"{row.get('id', '<unknown>')}@{row.get('version', '<unknown>')}: {row.get('name', '')}"
            )
            for kind in ("agents", "tools", "prompts"):
                values = row.get("components", {}).get(kind, ())
                if values:
                    print(f"  {kind}: {', '.join(str(value) for value in values)}")


def _validate_payload(directory: Path) -> tuple[int, dict[str, Any]]:
    report = validate_extension_directory(directory)
    payload = {
        "valid": report.valid,
        "errors": tuple(_issue_dict(issue) for issue in report.errors),
        "warnings": tuple(_issue_dict(issue) for issue in report.warnings),
    }
    return (0 if report.valid else 2), payload


def _component_ids(directory: Path, manifest: Any) -> dict[str, tuple[str, ...]]:
    components = manifest.components
    result: dict[str, tuple[str, ...]] = {
        "agents": tuple(component.id for component in components.agents),
        "tools": tuple(component.provider for component in components.tools),
        "prompts": (),
    }
    prompt_ids: list[str] = []
    for component in components.prompts:
        prompt_root = directory / component.directory
        index_path = directory / component.index
        data, errors = parse_prompt_directory(prompt_root, index_path)
        if data is not None and not errors:
            prompt_ids.extend(item.spec.id for item in data.prompts)
    result["prompts"] = tuple(prompt_ids)
    return result


def _list_payload(directory: Path) -> tuple[int, dict[str, Any]]:
    try:
        root = directory.expanduser().resolve(strict=True)
        validation = validate_extension_directory(root)
        if not validation.valid:
            return 2, {
                "valid": False,
                "directory": str(root),
                "errors": tuple(_issue_dict(issue) for issue in validation.errors),
                "warnings": tuple(_issue_dict(issue) for issue in validation.warnings),
            }
        manifest = load_manifest(root / MANIFEST_FILENAME)
        component_ids = _component_ids(root, manifest)
    except (OSError, ValueError, ExtensionManifestError) as exc:
        return 2, {
            "valid": False,
            "directory": str(directory),
            "errors": (
                {"path": "manifest", "message": str(exc), "code": "MANIFEST_INVALID"},
            ),
        }
    agents = tuple(component.id for component in manifest.components.agents)
    tools = tuple(component.provider for component in manifest.components.tools)
    return 0, {
        "valid": True,
        "directory": str(root),
        "id": manifest.id,
        "version": manifest.version,
        "api_version": manifest.api_version,
        "name": manifest.name,
        "description": manifest.description,
        "capabilities": tuple(manifest.capabilities.requested),
        "components": component_ids,
        # Flat keys retain the initial CLI JSON contract while components
        # provides resolved Prompt ids and a uniform grouped representation.
        "agents": agents,
        "tools": tools,
        "prompts": tuple(
            {
                "directory": component.directory,
                "index": component.index,
            }
            for component in manifest.components.prompts
        ),
    }


def _agent_contract_config(result: Any, descriptor: Any) -> dict[str, Any]:
    example = arguments_from_input_schema(dict(descriptor.config_schema))
    report = result.agents.validate_config(
        descriptor.id, example, version=descriptor.version
    )
    if not report.valid:
        issues = "; ".join(f"{issue.path}: {issue.message}" for issue in report.errors)
        raise AssertionError(f"agent {descriptor.id} config is invalid: {issues}")
    normalized = report.normalized_config
    if not isinstance(normalized, Mapping):
        raise AssertionError(
            f"agent {descriptor.id} normalized config must be a mapping"
        )
    return dict(normalized)


class _LoadedToolProvider:
    def __init__(self, tools: Iterable[Any]) -> None:
        self._tools = tuple(tools)

    def list_tools(self) -> tuple[Any, ...]:
        return self._tools


class _LoadedPromptProvider:
    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def list_prompts(self):
        return self._provider.list_prompts()

    def render(self, prompt_id, variables):
        return self._provider.render(prompt_id, variables)


def _test_payload(directory: Path) -> tuple[int, dict[str, Any]]:
    code, validation = _validate_payload(directory)
    if code:
        return code, {
            "passed": False,
            "directory": str(directory),
            "errors": tuple(
                f"{item.get('path') or 'manifest'}: {item.get('message')}"
                for item in validation["errors"]
            ),
        }
    root = directory.expanduser().resolve()
    try:
        result = load_extensions(ExtensionSettings(extension_roots=(root,)))
        records = [
            record for record in result.records if record.source.value == "external"
        ]
        if not records:
            raise AssertionError(f"no extension loaded from {root}")
        record = records[0]
        if record.status != ExtensionStatus.LOADED:
            details = "; ".join(issue.message for issue in record.errors)
            raise AssertionError(f"catalog load status is {record.status}: {details}")

        events = FakeEventSink()
        # Use the loaded Tool registry in the Agent build context so combined
        # examples exercise their real public Tool path.
        tool_runtime = build_fake_agent_build_context(
            tools=SynchronousToolInvoker(
                result.tools,
                account_id=1,
                decision_round_id="cli-test-round",
                trace_id="cli-test-trace",
                capabilities=frozenset(KNOWN_CAPABILITIES),
                events=events,
            ),
            prompts=result.prompts,
            events=events,
        )

        for descriptor in result.agents.list():
            registered = result.agents.get(descriptor.id, descriptor.version)
            config = _agent_contract_config(result, descriptor)
            assert_agent_contract(
                registered.factory,
                (
                    AgentCase(
                        name=descriptor.id,
                        context=build_fake_context(
                            account_id=1,
                            decision_round_id="cli-test-round",
                            trace_id="cli-test-trace",
                            config=config,
                        ),
                        config=config,
                        build_context=tool_runtime,
                    ),
                ),
            )

        loaded_tools = tuple(
            result.tools.get(spec.name).tool for spec in result.tools.list()
        )
        if loaded_tools:
            assert_tool_contract(_LoadedToolProvider(loaded_tools))

        providers: dict[int, Any] = {}
        for spec in result.prompts.list():
            provider = result.prompts.resolve(spec.id, spec.version).provider
            providers.setdefault(id(provider), provider)
        for provider in providers.values():
            assert_prompt_contract(_LoadedPromptProvider(provider))
    except Exception as exc:
        return 2, {
            "passed": False,
            "directory": str(root),
            "errors": (f"{type(exc).__name__}: {exc}",),
        }
    return 0, {
        "passed": True,
        "directory": str(root),
        "agents": tuple(descriptor.id for descriptor in result.agents.list()),
        "tools": tuple(spec.name for spec in result.tools.list()),
        "prompts": tuple(spec.id for spec in result.prompts.list()),
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command != "extension":  # pragma: no cover - parser guarantee
        return 2
    if arguments.extension_command == "validate":
        code, payload = _validate_payload(arguments.directory)
        _print_payload(payload, json_output=arguments.json_output)
        return code
    if arguments.extension_command == "test":
        code, payload = _test_payload(arguments.directory)
        _print_payload(payload, json_output=arguments.json_output)
        return code
    rows = []
    code = 0
    for directory in arguments.directory:
        item_code, payload = _list_payload(directory)
        code = max(code, item_code)
        rows.append(payload)
    payload: Any = rows[0] if arguments.json_output and len(rows) == 1 else rows
    _print_payload(payload, json_output=arguments.json_output)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main"]
