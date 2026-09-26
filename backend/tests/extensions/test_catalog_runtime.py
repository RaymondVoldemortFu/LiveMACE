from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

import pytest

from benchmark.agents import AgentDescriptor, AgentRegistry
from benchmark.contracts import (
    ExtensionLoadError,
    ExtensionRef,
    PromptProfileDescriptor,
    PromptSelection,
    PromptSpec,
)
from benchmark.extensions import (
    AccountRuntimeConfigDTO,
    ExtensionCatalog,
    ExtensionLoadResult,
    ExtensionSettings,
    ExtensionStatus,
    build_extension_runtime,
    load_extensions,
)
from benchmark.prompts import PromptRegistry, PromptSourcePriority
from benchmark.prompts.renderer import parse_template, render_template
from benchmark.testing import build_fake_build_context
from benchmark.tools import ToolRegistry


class _Factory:
    def create(self, context, config):
        raise AssertionError("not invoked by catalog tests")


class _PromptProvider:
    def __init__(self, version: str):
        self.spec = PromptSpec("com.example.prompt", version, ())
        self.template = parse_template(version)

    def list_prompts(self):
        return (self.spec,)

    def render(self, prompt_id, variables):
        return render_template(self.template, self.spec, variables)


def _multi_version_catalog() -> ExtensionCatalog:
    agents = AgentRegistry()
    for version, generation in (("1.0.0", "legacy"), ("2.0.0", "current")):
        agents.register(
            AgentDescriptor(
                "com.example.agent",
                version,
                {
                    "type": "object",
                    "properties": {
                        "generation": {
                            "type": "string",
                            "default": generation,
                        }
                    },
                    "additionalProperties": False,
                },
            ),
            _Factory(),
        )
    agents.freeze()

    prompts = PromptRegistry()
    extension = ExtensionRef("com.example.prompts", "1.0.0")
    for version in ("1.0.0", "2.0.0"):
        prompts.register_provider(
            extension,
            _PromptProvider(version),
            PromptSourcePriority.EXTERNAL,
        )
        prompts.register_profiles(
            extension,
            (
                PromptProfileDescriptor(
                    "com.example.profile",
                    version,
                    {
                        "system": PromptSelection(
                            "com.example.prompt",
                            version,
                        )
                    },
                ),
            ),
            PromptSourcePriority.EXTERNAL,
        )
    prompts.freeze()

    tools = ToolRegistry()
    tools.freeze()
    return ExtensionCatalog(
        ExtensionLoadResult(
            records=(),
            agents=agents,
            tools=tools,
            prompts=prompts,
        )
    )


def test_builtin_runtime_is_catalogued_and_frozen():
    runtime = build_extension_runtime(ExtensionSettings())

    records = runtime.catalog.list_extensions()
    assert [(record.id, record.status) for record in records] == [
        ("benchmark.core", ExtensionStatus.LOADED)
    ]
    assert [descriptor.id for descriptor in runtime.catalog.list_agents()] == [
        "core.advanced-multi-agent",
        "core.multi-agent",
        "core.react",
        "core.rule-aware",
    ]
    tool_names = {spec.name for spec in runtime.catalog.list_tools()}
    assert {
        "core.account_state",
        "core.decision_history",
        "core.kline_history",
        "core.market_snapshot",
        "core.memory_add",
        "core.memory_search",
        "core.search",
        "core.execute_shell_command",
        "core.read_file",
        "core.write_file",
        "core.run_python_script",
    }.issubset(tool_names)
    assert "core.execute_trade" in tool_names
    assert any(name.startswith("public.") for name in tool_names)
    assert len(runtime.catalog.list_prompt_profiles()) == 9
    assert runtime.agents.frozen
    assert runtime.tools.frozen
    assert runtime.prompts.frozen
    assert all("root" not in record.to_dict() for record in records)

    public_context = build_fake_build_context(prompts=runtime.prompts)
    for descriptor in runtime.catalog.list_agents():
        report = runtime.agents.validate_config(descriptor.id, {})
        assert report.valid, (descriptor.id, [issue.message for issue in report.errors])
        created = runtime.agents.get(descriptor.id).factory.create(
            public_context, report.normalized_config
        )
        assert callable(created.run)


def test_builtin_runtime_is_deterministic():
    first = build_extension_runtime(ExtensionSettings())
    second = build_extension_runtime(ExtensionSettings())

    assert [record.to_dict() for record in first.catalog.list_extensions()] == [
        record.to_dict() for record in second.catalog.list_extensions()
    ]
    assert first.catalog.list_agents() == second.catalog.list_agents()
    assert first.catalog.list_tools() == second.catalog.list_tools()
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

    invalid_schema = catalog.validate_account_config(
        AccountRuntimeConfigDTO(
            agent_id="core.react",
            agent_config={"unknown": True},
        )
    )
    assert not invalid_schema.valid
    assert {issue.code for issue in invalid_schema.errors} == {
        "additionalProperties"
    }

    invalid_components = catalog.validate_account_config(
        AccountRuntimeConfigDTO(
            agent_id="core.react",
            prompt_profile_id="core.missing",
            component_versions={"core.react": "9.0.0"},
        )
    )
    assert not invalid_components.valid
    assert {issue.code for issue in invalid_components.errors} >= {
        "AGENT_NOT_FOUND",
        "PROMPT_PROFILE_NOT_FOUND",
        "COMPONENT_VERSION_UNAVAILABLE",
    }


def test_catalog_uses_component_version_pins_for_agent_and_profile():
    report = _multi_version_catalog().validate_account_config(
        AccountRuntimeConfigDTO(
            agent_id="com.example.agent",
            prompt_profile_id="com.example.profile",
            component_versions={
                "com.example.agent": "1.0.0",
                "com.example.profile": "1.0.0",
            },
        )
    )

    assert report.valid
    assert report.normalized_config["agent_version"] == "1.0.0"
    assert report.normalized_config["agent_config"]["generation"] == "legacy"
    assert report.normalized_config["prompt_profile_version"] == "1.0.0"


def test_catalog_reports_conflicting_explicit_and_component_versions():
    report = _multi_version_catalog().validate_account_config(
        AccountRuntimeConfigDTO(
            agent_id="com.example.agent",
            agent_version="2.0.0",
            prompt_profile_id="com.example.profile",
            prompt_profile_version="2.0.0",
            component_versions={
                "com.example.agent": "1.0.0",
                "com.example.profile": "1.0.0",
            },
        )
    )

    assert not report.valid
    assert [issue.code for issue in report.errors] == [
        "COMPONENT_VERSION_CONFLICT",
        "COMPONENT_VERSION_CONFLICT",
    ]
    assert report.normalized_config["agent_version"] == "2.0.0"
    assert report.normalized_config["agent_config"]["generation"] == "current"
    assert report.normalized_config["prompt_profile_version"] == "2.0.0"


def test_runtime_config_to_mapping_deeply_restores_json_values():
    dto = AccountRuntimeConfigDTO(
        agent_id="com.example.agent",
        agent_config={
            "strategy": {
                "windows": [5, 20],
                "filters": [{"field": "volume", "enabled": True}],
            }
        },
        toolset_ids=("com.example.tools",),
        disabled_tools=("com.example.tools.news",),
        component_versions={"com.example.agent": "1.0.0"},
    )

    mapping = dto.to_mapping()
    serialized = json.dumps(mapping)
    restored = AccountRuntimeConfigDTO.from_mapping(json.loads(serialized))

    assert isinstance(mapping["agent_config"]["strategy"], dict)
    assert isinstance(mapping["agent_config"]["strategy"]["windows"], list)
    assert restored.to_mapping() == mapping


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
            disabled_tools=("core.missing_trade",),
        )
    )
    assert not disabled.valid
    assert disabled.errors[0].code == "TOOL_NOT_FOUND"


def test_unavailable_toolsets_are_rejected():
    catalog = build_extension_runtime(ExtensionSettings()).catalog
    report = catalog.validate_account_config(
        AccountRuntimeConfigDTO(
            agent_id="core.react",
            toolset_ids=("core.missing-tools",),
        )
    )

    assert not report.valid
    assert [issue.code for issue in report.errors] == ["TOOLSET_NOT_FOUND"]


def test_entrypoint_module_must_be_inside_extension_root(tmp_path: Path):
    root = tmp_path / "outside-entrypoint"
    root.mkdir()
    (root / "schema.json").write_text('{"type": "object"}', encoding="utf-8")
    (root / "livemace-bench-extension.yaml").write_text(
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
        (root / "livemace-bench-extension.yaml").write_text(
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
    (broken / "livemace-bench-extension.yaml").write_text(
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
