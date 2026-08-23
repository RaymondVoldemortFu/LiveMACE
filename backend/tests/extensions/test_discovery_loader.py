from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from benchmark.contracts import ExtensionLoadError
from benchmark.extensions import (
    ExtensionSettings,
    ExtensionSource,
    ExtensionStatus,
    discover_extensions,
    load_extensions,
)


def _write_manifest(root: Path, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "alpha-arena-extension.yaml").write_text(body, encoding="utf-8")


def _prompt_extension(
    root: Path,
    extension_id: str = "com.example.prompts",
    prompt_id: str = "com.example.system",
) -> None:
    prompts = root / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "system.txt").write_text("hello {name}", encoding="utf-8")
    (prompts / "index.yaml").write_text(
        "api_version: 1\n"
        "prompts:\n"
        f"  - id: {prompt_id}\n"
        "    version: 1.0.0\n"
        "    file: system.txt\n"
        "    required_variables: [name]\n",
        encoding="utf-8",
    )
    _write_manifest(
        root,
        "api_version: 1\n"
        f"id: {extension_id}\n"
        "version: 1.0.0\n"
        "name: Prompt Extension\n"
        "components:\n"
        "  prompts:\n"
        "    - directory: prompts\n"
        "      index: prompts/index.yaml\n",
    )


def _agent_extension(
    root: Path,
    module_name: str,
    module_body: str,
    *,
    extension_id: str = "com.example.agent",
    python_requires: str = ">=3.10",
    python_entrypoint: str = "metadata:entrypoint",
    include_tool: bool = False,
) -> None:
    schema = root / "schema.json"
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    (root / f"{module_name}.py").write_text(module_body, encoding="utf-8")
    tool_block = (
        "  tools:\n"
        f"    - provider: {module_name}:create_provider\n"
        if include_tool
        else ""
    )
    _write_manifest(
        root,
        "api_version: 1\n"
        f"id: {extension_id}\n"
        "version: 1.0.0\n"
        "name: Agent Extension\n"
        "python:\n"
        f"  requires: '{python_requires}'\n"
        f"  entrypoint: {python_entrypoint}\n"
        "components:\n"
        "  agents:\n"
        f"    - id: {extension_id}.component\n"
        f"      factory: {module_name}:create_factory\n"
        "      config_schema: schema.json\n"
        f"{tool_block}",
    )


def test_discovery_is_builtin_first_and_path_sorted(tmp_path):
    builtin = tmp_path / "builtin"
    first = tmp_path / "a-extension"
    second = tmp_path / "b-extension"
    settings = ExtensionSettings(
        builtin_root=builtin,
        extension_roots=(second, first),
    )

    candidates = discover_extensions(settings)

    assert [candidate.source for candidate in candidates] == [
        ExtensionSource.BUILTIN,
        ExtensionSource.EXTERNAL,
        ExtensionSource.EXTERNAL,
    ]
    assert [candidate.root for candidate in candidates] == [
        builtin.resolve(),
        first.resolve(),
        second.resolve(),
    ]


def test_discovery_deduplicates_using_host_path_case_semantics(tmp_path):
    upper = tmp_path / "CaseExtension"
    lower = tmp_path / "caseextension"

    candidates = discover_extensions(
        ExtensionSettings(extension_roots=(lower, upper))
    )

    expected_count = len(
        {
            os.path.normcase(str(upper.resolve())),
            os.path.normcase(str(lower.resolve())),
        }
    )
    assert len(candidates) == expected_count
    assert [str(candidate.root) for candidate in candidates] == sorted(
        (str(candidate.root) for candidate in candidates),
        key=lambda path: (os.path.normcase(path), path),
    )


def test_disabled_extension_is_not_imported(tmp_path):
    sentinel = tmp_path / "imported.txt"
    root = tmp_path / "disabled"
    _agent_extension(
        root,
        "disabled_module",
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('imported')\n"
        "class Factory:\n"
        "    def create(self, context, config):\n"
        "        raise AssertionError\n"
        "def create_factory():\n"
        "    return Factory()\n",
        extension_id="com.example.disabled",
    )

    result = load_extensions(
        ExtensionSettings(
            extension_roots=(root,),
            disabled_extensions=frozenset({"com.example.disabled"}),
        )
    )

    assert result.records[0].status == ExtensionStatus.DISABLED
    assert not sentinel.exists()
    assert result.agents.list() == ()


def test_prompt_only_extension_loads_without_python(tmp_path):
    root = tmp_path / "prompts"
    _prompt_extension(root)

    result = load_extensions(ExtensionSettings(extension_roots=(root,)))

    assert result.records[0].status == ExtensionStatus.LOADED
    assert result.prompts.frozen
    assert result.prompts.render("com.example.system", {"name": "Ada"}).content == "hello Ada"


def test_static_invalid_extension_does_not_import_declared_module(tmp_path):
    sentinel = tmp_path / "imported.txt"
    root = tmp_path / "invalid"
    _agent_extension(
        root,
        "invalid_module",
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('imported')\n"
        "def create_factory():\n"
        "    raise AssertionError\n",
        extension_id="com.example.invalid",
    )
    (root / "schema.json").write_text("{invalid", encoding="utf-8")

    result = load_extensions(ExtensionSettings(extension_roots=(root,)))

    assert result.records[0].status == ExtensionStatus.INVALID
    assert not sentinel.exists()


def test_python_entrypoint_is_metadata_only(tmp_path):
    sentinel = tmp_path / "metadata-imported.txt"
    root = tmp_path / "agent"
    metadata = root / "metadata.py"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('imported')\n"
        "def entrypoint():\n"
        "    return None\n",
        encoding="utf-8",
    )
    _agent_extension(
        root,
        "component_module",
        "class Factory:\n"
        "    def create(self, context, config):\n"
        "        return None\n"
        "def create_factory():\n"
        "    return Factory()\n",
        python_entrypoint="metadata:entrypoint",
    )

    result = load_extensions(ExtensionSettings(extension_roots=(root,)))

    assert result.records[0].status == ExtensionStatus.LOADED
    assert not sentinel.exists()


def test_incompatible_python_and_capabilities_do_not_import(tmp_path):
    root = tmp_path / "incompatible"
    _prompt_extension(root, extension_id="com.example.incompatible")
    manifest = root / "alpha-arena-extension.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "components:\n", "python:\n  requires: '>99'\n  entrypoint: metadata:entrypoint\ncomponents:\n"
        ).replace(
            "components:\n", "capabilities:\n  requested: [market.read]\ncomponents:\n"
        ),
        encoding="utf-8",
    )

    result = load_extensions(
        ExtensionSettings(
            extension_roots=(root,),
            allowed_capabilities=frozenset(),
        )
    )

    assert result.records[0].status == ExtensionStatus.INCOMPATIBLE
    assert result.records[0].errors[0].code == "CAPABILITY_NOT_ALLOWED"


def test_incompatible_python_requirement_is_reported(tmp_path):
    root = tmp_path / "python-incompatible"
    _prompt_extension(root, extension_id="com.example.python-incompatible")
    manifest = root / "alpha-arena-extension.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "components:\n",
            "python:\n  requires: '>99'\n  entrypoint: metadata:entrypoint\ncomponents:\n",
        ),
        encoding="utf-8",
    )

    result = load_extensions(ExtensionSettings(extension_roots=(root,)))

    assert result.records[0].status == ExtensionStatus.INCOMPATIBLE
    assert result.records[0].errors[0].code == "PYTHON_VERSION_UNSUPPORTED"


def test_failed_external_extension_does_not_block_valid_extension(tmp_path):
    bad = tmp_path / "bad"
    good = tmp_path / "good"
    _agent_extension(
        bad,
        "shared_module",
        "def create_factory():\n"
        "    return object()\n",
        extension_id="com.example.bad",
    )
    _agent_extension(
        good,
        "shared_module",
        "class Factory:\n"
        "    def create(self, context, config):\n"
        "        return None\n"
        "def create_factory():\n"
        "    return Factory()\n",
        extension_id="com.example.good",
    )

    result = load_extensions(ExtensionSettings(extension_roots=(good, bad)))

    assert [record.status for record in result.records] == [
        ExtensionStatus.LOAD_FAILED,
        ExtensionStatus.LOADED,
    ]
    assert [descriptor.id for descriptor in result.agents.list()] == [
        "com.example.good.component"
    ]


def test_unresolved_external_profile_does_not_block_valid_extension(tmp_path):
    bad = tmp_path / "bad-profile"
    good = tmp_path / "good-profile"
    _prompt_extension(
        bad,
        extension_id="com.example.bad-profile",
        prompt_id="com.example.bad-system",
    )
    bad_index = bad / "prompts" / "index.yaml"
    bad_index.write_text(
        bad_index.read_text(encoding="utf-8")
        + "profiles:\n"
        "  - id: com.example.bad-profile.default\n"
        "    version: 1.0.0\n"
        "    slots:\n"
        "      system:\n"
        "        prompt_id: com.example.missing\n",
        encoding="utf-8",
    )
    _prompt_extension(
        good,
        extension_id="com.example.good-profile",
        prompt_id="com.example.good-system",
    )

    result = load_extensions(ExtensionSettings(extension_roots=(good, bad)))

    assert [record.status for record in result.records] == [
        ExtensionStatus.LOAD_FAILED,
        ExtensionStatus.LOADED,
    ]
    assert result.records[0].errors[0].code == "PROMPT_PROFILE_UNRESOLVED"
    assert [prompt.id for prompt in result.prompts.list()] == [
        "com.example.good-system"
    ]


def test_failed_extension_rolls_back_imported_modules(tmp_path):
    module_name = "phase3_shared_module"
    dependency_name = "phase3_shared_dependency"
    bad = tmp_path / "bad-module"
    good = tmp_path / "good-module"
    _agent_extension(
        bad,
        module_name,
        f"import {dependency_name}\n"
        f"MARKER = {dependency_name}.MARKER\n"
        "def create_factory():\n"
        "    return object()\n",
        extension_id="com.example.bad-module",
    )
    _agent_extension(
        good,
        module_name,
        f"import {dependency_name}\n"
        f"MARKER = {dependency_name}.MARKER\n"
        "class Factory:\n"
        "    def create(self, context, config):\n"
        f"        import {module_name}\n"
        f"        return {module_name}.MARKER\n"
        "def create_factory():\n"
        "    return Factory()\n",
        extension_id="com.example.good-module",
    )
    (bad / f"{dependency_name}.py").write_text(
        "MARKER = 'BAD'\n", encoding="utf-8"
    )
    (good / f"{dependency_name}.py").write_text(
        "MARKER = 'GOOD'\n", encoding="utf-8"
    )

    sys.modules.pop(module_name, None)
    sys.modules.pop(dependency_name, None)
    try:
        result = load_extensions(ExtensionSettings(extension_roots=(good, bad)))

        assert [record.status for record in result.records] == [
            ExtensionStatus.LOAD_FAILED,
            ExtensionStatus.LOADED,
        ]
        factory = result.agents.get("com.example.good-module.component").factory
        assert factory.create(None, {}) == "GOOD"
        assert sys.modules[module_name].MARKER == "GOOD"
    finally:
        sys.modules.pop(module_name, None)
        sys.modules.pop(dependency_name, None)


def test_tool_capabilities_must_be_declared_by_manifest(tmp_path):
    root = tmp_path / "undeclared-capability"
    _agent_extension(
        root,
        "undeclared_capability_module",
        "from benchmark.contracts import SideEffect, ToolResult, ToolSpec\n"
        "class Factory:\n"
        "    def create(self, context, config):\n"
        "        return None\n"
        "class Tool:\n"
        "    spec = ToolSpec(\n"
        "        name='com.example.capability.read',\n"
        "        description='read network data',\n"
        "        input_schema={'type': 'object'},\n"
        "        output_schema={'type': 'object'},\n"
        "        side_effect=SideEffect.EXTERNAL_READ,\n"
        "        required_capabilities=('network.read',),\n"
        "    )\n"
        "    def invoke(self, context, arguments):\n"
        "        return ToolResult(ok=True, value={})\n"
        "class Provider:\n"
        "    def list_tools(self):\n"
        "        return (Tool(),)\n"
        "def create_factory():\n"
        "    return Factory()\n"
        "def create_provider():\n"
        "    return Provider()\n",
        extension_id="com.example.undeclared-capability",
        include_tool=True,
    )

    result = load_extensions(
        ExtensionSettings(
            extension_roots=(root,),
            allowed_capabilities=frozenset(),
        )
    )

    assert result.records[0].status == ExtensionStatus.LOAD_FAILED
    assert result.records[0].errors[0].code == "TOOL_CAPABILITY_UNDECLARED"
    assert result.tools.list() == ()


def test_failed_extension_is_atomic_across_components(tmp_path):
    root = tmp_path / "partial"
    _agent_extension(
        root,
        "partial_module",
        "class Factory:\n"
        "    def create(self, context, config):\n"
        "        return None\n"
        "def create_factory():\n"
        "    return Factory()\n"
        "def create_provider():\n"
        "    return object()\n",
        extension_id="com.example.partial",
        include_tool=True,
    )

    result = load_extensions(ExtensionSettings(extension_roots=(root,)))

    assert result.records[0].status == ExtensionStatus.LOAD_FAILED
    assert result.agents.list() == ()
    assert result.tools.list() == ()


def test_builtin_failure_is_fatal(tmp_path):
    builtin = tmp_path / "builtin"
    _write_manifest(
        builtin,
        "api_version: 1\n"
        "id: com.example.builtin\n"
        "version: 1.0.0\n"
        "name: Broken Builtin\n"
        "components:\n"
        "  prompts:\n"
        "    - directory: missing\n"
        "      index: missing/index.yaml\n",
    )

    with pytest.raises(ExtensionLoadError) as caught:
        load_extensions(ExtensionSettings(builtin_root=builtin))

    assert caught.value.code == "BUILTIN_EXTENSION_LOAD_FAILED"


def test_loader_rejects_manifest_symlink_outside_extension_root(tmp_path):
    outside = tmp_path / "outside-manifest"
    outside.mkdir()
    real_manifest = outside / "real-manifest.yaml"
    real_manifest.write_text(
        "api_version: 1\n"
        "id: com.example.escaped-manifest\n"
        "version: 1.0.0\n"
        "name: Escaped Manifest\n",
        encoding="utf-8",
    )
    root = tmp_path / "escaped-manifest"
    root.mkdir()
    (root / "alpha-arena-extension.yaml").symlink_to(real_manifest)

    result = load_extensions(ExtensionSettings(extension_roots=(root,)))

    assert result.records[0].status == ExtensionStatus.INVALID
    assert result.records[0].errors[0].code == "MANIFEST_PATH_INVALID"
