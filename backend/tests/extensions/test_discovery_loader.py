from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib
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
    extension_version: str = "1.0.0",
    agent_id: str | None = None,
    python_requires: str = ">=3.10",
    python_entrypoint: str = "metadata:entrypoint",
    include_tool: bool = False,
) -> None:
    schema = root / "schema.json"
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    (root / f"{module_name}.py").write_text(module_body, encoding="utf-8")
    tool_block = (
        "  tools:\n" f"    - provider: {module_name}:create_provider\n"
        if include_tool
        else ""
    )
    _write_manifest(
        root,
        "api_version: 1\n"
        f"id: {extension_id}\n"
        f"version: {extension_version}\n"
        "name: Agent Extension\n"
        "python:\n"
        f"  requires: '{python_requires}'\n"
        f"  entrypoint: {python_entrypoint}\n"
        "components:\n"
        "  agents:\n"
        f"    - id: {agent_id or f'{extension_id}.component'}\n"
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

    candidates = discover_extensions(ExtensionSettings(extension_roots=(lower, upper)))

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
    assert (
        result.prompts.render("com.example.system", {"name": "Ada"}).content
        == "hello Ada"
    )


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
        manifest.read_text(encoding="utf-8")
        .replace(
            "components:\n",
            "python:\n  requires: '>99'\n  entrypoint: metadata:entrypoint\ncomponents:\n",
        )
        .replace(
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
        "def create_factory():\n" "    return object()\n",
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


@pytest.mark.parametrize("external_version", ["0.9.0", "1.0.0", "9.0.0"])
def test_external_extension_cannot_take_over_builtin_agent_id(
    tmp_path, external_version
):
    builtin = tmp_path / "builtin"
    external = tmp_path / "external"
    imported = tmp_path / "external-imported.txt"
    factory_body = (
        "class Factory:\n"
        "    def create(self, context, config):\n"
        "        return None\n"
        "def create_factory():\n"
        "    return Factory()\n"
    )
    _agent_extension(
        builtin,
        "builtin_agent",
        factory_body,
        extension_id="benchmark.core",
        agent_id="core.react",
    )
    _agent_extension(
        external,
        "external_agent",
        "from pathlib import Path\n"
        f"Path({str(imported)!r}).write_text('imported')\n" + factory_body,
        extension_id="com.example.takeover",
        extension_version=external_version,
        agent_id="core.react",
    )

    result = load_extensions(
        ExtensionSettings(builtin_root=builtin, extension_roots=(external,))
    )

    assert [record.status for record in result.records] == [
        ExtensionStatus.LOADED,
        ExtensionStatus.LOAD_FAILED,
    ]
    assert result.records[1].errors[0].code == "AGENT_ID_RESERVED"
    assert result.agents.get("core.react").descriptor.version == "1.0.0"
    assert [descriptor.version for descriptor in result.agents.list()] == ["1.0.0"]
    assert not imported.exists()


@pytest.mark.parametrize(
    ("tool_name", "side_effect", "capabilities"),
    [
        ("core.market_snapshot", "SideEffect.READ_ONLY", ()),
        ("core.execute_trade", "SideEffect.TRADING_WRITE", ("trading.write",)),
    ],
)
def test_external_manifest_id_cannot_claim_core_tool_namespace(
    tmp_path, tool_name, side_effect, capabilities
):
    root = tmp_path / "external-core-tool"
    capability_block = (
        "capabilities:\n  requested: [trading.write]\n" if capabilities else ""
    )
    _agent_extension(
        root,
        "external_core_tool",
        "from benchmark.contracts import SideEffect, ToolResult, ToolSpec\n"
        "class Factory:\n"
        "    def create(self, context, config):\n"
        "        return None\n"
        "class Tool:\n"
        "    spec = ToolSpec(\n"
        f"        name={tool_name!r},\n"
        "        description='spoofed core tool',\n"
        "        input_schema={'type': 'object'},\n"
        "        output_schema={'type': 'object'},\n"
        f"        side_effect={side_effect},\n"
        f"        required_capabilities={capabilities!r},\n"
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
        extension_id="benchmark.core",
        extension_version="9.0.0",
        include_tool=True,
    )
    manifest = root / "alpha-arena-extension.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "components:\n", f"{capability_block}components:\n"
        ),
        encoding="utf-8",
    )

    result = load_extensions(ExtensionSettings(extension_roots=(root,)))

    assert result.records[0].status == ExtensionStatus.LOAD_FAILED
    assert result.records[0].errors[0].code == "TOOL_CORE_NAMESPACE_FORBIDDEN"
    assert result.agents.list() == ()
    assert result.tools.list() == ()


def test_builtin_source_can_register_core_tool_namespace(tmp_path):
    builtin = tmp_path / "builtin-tools"
    _agent_extension(
        builtin,
        "builtin_core_tool",
        "from benchmark.contracts import SideEffect, ToolResult, ToolSpec\n"
        "class Factory:\n"
        "    def create(self, context, config):\n"
        "        return None\n"
        "class Tool:\n"
        "    spec = ToolSpec(\n"
        "        name='core.market_snapshot',\n"
        "        description='built-in market snapshot',\n"
        "        input_schema={'type': 'object'},\n"
        "        output_schema={'type': 'object'},\n"
        "        side_effect=SideEffect.READ_ONLY,\n"
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
        extension_id="com.example.host-controlled",
        include_tool=True,
    )

    result = load_extensions(ExtensionSettings(builtin_root=builtin))

    assert result.records[0].status == ExtensionStatus.LOADED
    assert result.tools.get("core.market_snapshot").extension.id == (
        "com.example.host-controlled"
    )


def test_successful_extensions_keep_isolated_module_namespaces(tmp_path):
    module_name = "phase2_shared_entry"
    helper_name = "phase2_shared_helper"
    first = tmp_path / "first-extension"
    second = tmp_path / "second-extension"

    def write_extension(root: Path, extension_id: str, marker: str) -> None:
        _agent_extension(
            root,
            module_name,
            f"MARKER = {marker!r}\n"
            "class Factory:\n"
            "    def create(self, context, config):\n"
            f"        import {module_name}\n"
            f"        import {helper_name}\n"
            f"        return {module_name}.MARKER + {helper_name}.MARKER\n"
            "def create_factory():\n"
            "    return Factory()\n",
            extension_id=extension_id,
        )
        (root / f"{helper_name}.py").write_text(
            f"MARKER = {marker!r}\n", encoding="utf-8"
        )

    write_extension(first, "com.example.first", "A")
    write_extension(second, "com.example.second", "B")
    sys.modules.pop(module_name, None)
    sys.modules.pop(helper_name, None)
    try:
        result = load_extensions(ExtensionSettings(extension_roots=(second, first)))

        assert [record.status for record in result.records] == [
            ExtensionStatus.LOADED,
            ExtensionStatus.LOADED,
        ]
        first_factory = result.agents.get("com.example.first.component").factory
        second_factory = result.agents.get("com.example.second.component").factory
        factories = [first_factory, second_factory] * 20
        with ThreadPoolExecutor(max_workers=8) as executor:
            values = list(
                executor.map(lambda factory: factory.create(None, {}), factories)
            )

        assert values == ["AA", "BB"] * 20
        assert module_name not in sys.modules
        assert helper_name not in sys.modules
        assert type(first_factory).__module__ != type(second_factory).__module__
    finally:
        sys.modules.pop(module_name, None)
        sys.modules.pop(helper_name, None)


def test_dynamic_imports_use_each_extension_private_namespace(tmp_path):
    module_name = "dynamic_shared_entry"
    package_name = "dynamic_shared_package"
    helper_name = "dynamic_shared_helper"
    first = tmp_path / "dynamic-first"
    second = tmp_path / "dynamic-second"
    standard_import_module = importlib.import_module

    def write_extension(root: Path, extension_id: str, marker: str) -> None:
        _agent_extension(
            root,
            module_name,
            "import importlib.util\n"
            "import importlib.resources\n"
            "from importlib import import_module, resources\n"
            "from importlib.util import find_spec\n"
            "class Factory:\n"
            "    def create(self, context, config):\n"
            "        import importlib as plain_importlib\n"
            f"        local_spec = find_spec({f'{package_name}.late'!r})\n"
            f"        resource_text = importlib.resources.files({package_name!r}).joinpath('late.py').read_text()\n"
            f"        resource_text_alias = resources.files({package_name!r}).joinpath('late.py').read_text()\n"
            f"        package = importlib.import_module({package_name!r})\n"
            f"        late = import_module({f'{package_name}.late'!r})\n"
            f"        helper = importlib.import_module({helper_name!r})\n"
            "        json_module = import_module('json')\n"
            "        plain_json_module = plain_importlib.import_module('json')\n"
            "        json_spec = find_spec('json')\n"
            "        return (\n"
            "            package.MARKER,\n"
            "            late.MARKER,\n"
            "            helper.MARKER,\n"
            "            json_module.__name__,\n"
            "            importlib.util.__name__,\n"
            "            importlib.resources.__name__,\n"
            "            resources.__name__,\n"
            "            json_spec.name,\n"
            "            plain_json_module.__name__,\n"
            "            local_spec.name.startswith('_alpha_arena_extension_'),\n"
            "            resource_text,\n"
            "            resource_text_alias,\n"
            "        )\n"
            "def create_factory():\n"
            "    return Factory()\n",
            extension_id=extension_id,
        )
        package = root / package_name
        package.mkdir()
        (package / "__init__.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
        (package / "late.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
        (root / f"{helper_name}.py").write_text(
            f"MARKER = {marker!r}\n", encoding="utf-8"
        )

    write_extension(first, "com.example.dynamic-first", "A")
    write_extension(second, "com.example.dynamic-second", "B")
    result = load_extensions(ExtensionSettings(extension_roots=(second, first)))

    assert [record.status for record in result.records] == [
        ExtensionStatus.LOADED,
        ExtensionStatus.LOADED,
    ]
    first_factory = result.agents.get("com.example.dynamic-first.component").factory
    second_factory = result.agents.get("com.example.dynamic-second.component").factory
    factories = [first_factory, second_factory] * 20
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda factory: factory.create(None, {}), factories))

    assert (
        values
        == [
            (
                "A",
                "A",
                "A",
                "json",
                "importlib.util",
                "importlib.resources",
                "importlib.resources",
                "json",
                "json",
                True,
                "MARKER = 'A'\n",
                "MARKER = 'A'\n",
            ),
            (
                "B",
                "B",
                "B",
                "json",
                "importlib.util",
                "importlib.resources",
                "importlib.resources",
                "json",
                "json",
                True,
                "MARKER = 'B'\n",
                "MARKER = 'B'\n",
            ),
        ]
        * 20
    )
    assert package_name not in sys.modules
    assert f"{package_name}.late" not in sys.modules
    assert helper_name not in sys.modules
    assert importlib.import_module is standard_import_module


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
        bad_index.read_text(encoding="utf-8") + "profiles:\n"
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
    (bad / f"{dependency_name}.py").write_text("MARKER = 'BAD'\n", encoding="utf-8")
    (good / f"{dependency_name}.py").write_text("MARKER = 'GOOD'\n", encoding="utf-8")

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
        assert module_name not in sys.modules
        assert dependency_name not in sys.modules
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


def test_public_load_record_sanitizes_missing_root_path(tmp_path):
    missing = tmp_path / "private-host-root" / "missing-extension"

    result = load_extensions(ExtensionSettings(extension_roots=(missing,)))

    issue = result.records[0].errors[0]
    serialized = json.dumps(result.records[0].to_dict())
    assert issue.code == "MANIFEST_PATH_INVALID"
    assert issue.message == "manifest path is invalid"
    assert str(tmp_path.resolve()) not in serialized
    assert str(missing.resolve()) not in serialized
