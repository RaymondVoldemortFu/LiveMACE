from __future__ import annotations

import importlib
from pathlib import Path
import sys

import pytest

from benchmark.contracts import ExtensionLoadError
from benchmark.extensions import (
    AccountRuntimeConfigDTO,
    ExtensionSettings,
    ExtensionStatus,
    build_extension_runtime,
    load_extensions,
)


def test_builtin_runtime_is_catalogued_and_frozen():
    runtime = build_extension_runtime(ExtensionSettings())

    records = runtime.catalog.list_extensions()
    assert [(record.id, record.status) for record in records] == [
        ("benchmark.core", ExtensionStatus.LOADED)
    ]
    assert [descriptor.id for descriptor in runtime.catalog.list_agents()] == [
        "core.react",
        "core.rule-aware",
    ]
    assert len(runtime.catalog.list_prompt_profiles()) == 9
    assert runtime.agents.frozen
    assert runtime.tools.frozen
    assert runtime.prompts.frozen
    assert all("root" not in record.to_dict() for record in records)


def test_builtin_runtime_is_deterministic():
    first = build_extension_runtime(ExtensionSettings())
    second = build_extension_runtime(ExtensionSettings())

    assert [record.to_dict() for record in first.catalog.list_extensions()] == [
        record.to_dict() for record in second.catalog.list_extensions()
    ]
    assert first.catalog.list_agents() == second.catalog.list_agents()
    assert first.catalog.list_prompt_profiles() == second.catalog.list_prompt_profiles()


def test_catalog_validates_agent_schema_profile_and_versions():
    catalog = build_extension_runtime(ExtensionSettings()).catalog
    config = AccountRuntimeConfigDTO(
        agent_id="core.react",
        agent_config={},
        prompt_profile_id="core.react.default",
        component_versions={
            "core.react": "1.0.0",
            "core.react.default": "1.0.0",
        },
    )

    report = catalog.validate_account_config(config)

    assert report.valid
    assert report.normalized_config["agent_version"] == "1.0.0"
    assert report.normalized_config["agent_config"]["max_steps"] == 100
    assert report.normalized_config["agent_config"]["include_simulation_notice"] is False

    invalid = catalog.validate_account_config(
        AccountRuntimeConfigDTO(
            agent_id="core.react",
            agent_config={"unknown": True},
            prompt_profile_id="core.missing",
            component_versions={"core.react": "9.0.0"},
        )
    )
    assert not invalid.valid
    assert {issue.code for issue in invalid.errors} >= {
        "additionalProperties",
        "PROMPT_PROFILE_NOT_FOUND",
        "COMPONENT_VERSION_UNAVAILABLE",
    }


def test_catalog_rejects_unknown_fields_and_tool_names():
    catalog = build_extension_runtime(ExtensionSettings()).catalog

    unknown = catalog.validate_account_config(
        {"agent_id": "core.react", "api_key": "should-not-be-config"}
    )
    assert not unknown.valid
    assert unknown.errors[0].code == "ACCOUNT_CONFIG_INVALID"

    disabled = catalog.validate_account_config(
        AccountRuntimeConfigDTO(
            agent_id="core.react",
            disabled_tools=("core.execute_trade",),
        )
    )
    assert not disabled.valid
    assert disabled.errors[0].code == "TOOL_NOT_FOUND"


def test_toolset_validation_is_non_blocking_until_m06_m12():
    catalog = build_extension_runtime(ExtensionSettings()).catalog
    report = catalog.validate_account_config(
        AccountRuntimeConfigDTO(
            agent_id="core.react",
            toolset_ids=("core.default-tools",),
        )
    )

    assert report.valid
    assert [issue.code for issue in report.warnings] == [
        "TOOLSET_VALIDATION_DEFERRED"
    ]


def test_entrypoint_module_must_be_inside_extension_root(tmp_path: Path):
    root = tmp_path / "outside-entrypoint"
    root.mkdir()
    (root / "schema.json").write_text('{"type": "object"}', encoding="utf-8")
    (root / "alpha-arena-extension.yaml").write_text(
        "api_version: 1\n"
        "id: com.example.outside\n"
        "version: 1.0.0\n"
        "name: Outside Entrypoint\n"
        "python:\n"
        "  requires: '>=3.10'\n"
        "  entrypoint: json:loads\n"
        "components:\n"
        "  agents:\n"
        "    - id: com.example.outside.agent\n"
        "      factory: json:loads\n"
        "      config_schema: schema.json\n",
        encoding="utf-8",
    )

    result = load_extensions(ExtensionSettings(extension_roots=(root,)))

    assert result.records[0].status == ExtensionStatus.LOAD_FAILED
    assert result.records[0].errors[0].code == "ENTRYPOINT_MODULE_OUTSIDE_ROOT"


def test_failed_replacement_restores_existing_module(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    module_name = "collision_module"
    (outside / f"{module_name}.py").write_text(
        "marker = 'outside'\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(outside))
    try:
        existing = importlib.import_module(module_name)
        root = tmp_path / "extension"
        root.mkdir()
        (root / "schema.json").write_text('{"type": "object"}', encoding="utf-8")
        (root / "alpha-arena-extension.yaml").write_text(
            "api_version: 1\n"
            "id: com.example.collision\n"
            "version: 1.0.0\n"
            "name: Collision Extension\n"
            "python:\n"
            "  requires: '>=3.10'\n"
            "  entrypoint: collision_module:factory\n"
            "components:\n"
            "  agents:\n"
            "    - id: com.example.collision.agent\n"
            "      factory: collision_module:factory\n"
            "      config_schema: schema.json\n",
            encoding="utf-8",
        )

        result = load_extensions(ExtensionSettings(extension_roots=(root,)))

        assert result.records[0].status == ExtensionStatus.LOAD_FAILED
        assert sys.modules[module_name] is existing
        assert sys.modules[module_name].marker == "outside"
    finally:
        sys.path.remove(str(outside))
        sys.modules.pop(module_name, None)


def test_builtin_failure_remains_fatal_when_runtime_uses_builtin_root(tmp_path: Path):
    broken = tmp_path / "broken-builtin"
    broken.mkdir()
    (broken / "alpha-arena-extension.yaml").write_text(
        "api_version: 1\n"
        "id: benchmark.core\n"
        "version: 1.0.0\n"
        "name: Broken Builtin\n"
        "components:\n"
        "  prompts:\n"
        "    - directory: missing\n"
        "      index: missing/index.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(ExtensionLoadError) as caught:
        load_extensions(ExtensionSettings(builtin_root=broken))

    assert caught.value.code == "BUILTIN_EXTENSION_LOAD_FAILED"
