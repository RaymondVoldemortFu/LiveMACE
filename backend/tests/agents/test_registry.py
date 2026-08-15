from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from benchmark.agents import (
    AgentDescriptor,
    AgentRegistry,
    AgentRegistryFrozenError,
    ComponentConfigError,
    ComponentConflictError,
    ComponentNotFoundError,
)


class Factory:
    def create(self, context, config):
        raise AssertionError("not invoked by registry tests")


SCHEMA = {
    "type": "object",
    "properties": {
        "max_steps": {"type": "integer", "minimum": 1, "default": 5},
        "profile": {
            "type": "object",
            "properties": {"mode": {"type": "string", "default": "safe"}},
            "default": {},
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


def descriptor(version="1.0.0"):
    return AgentDescriptor("com.example.agent", version, SCHEMA)


def test_registry_resolves_latest_version_and_rejects_duplicates():
    registry = AgentRegistry()
    registry.register(descriptor("1.0.0"), Factory())
    registry.register(descriptor("1.2.0"), Factory())
    registry.register(descriptor("2.0.0-beta.2"), Factory())
    registry.register(descriptor("2.0.0-beta.1"), Factory())
    registry.register(descriptor("2.0.0-beta.11"), Factory())

    assert registry.get("com.example.agent").descriptor.version == "2.0.0-beta.11"
    assert registry.get("com.example.agent", "1.0.0").descriptor.version == "1.0.0"
    assert [item.version for item in registry.list()] == [
        "1.0.0",
        "1.2.0",
        "2.0.0-beta.1",
        "2.0.0-beta.2",
        "2.0.0-beta.11",
    ]
    with pytest.raises(ComponentConflictError) as caught:
        registry.register(descriptor("1.0.0"), Factory())
    assert caught.value.code == "AGENT_VERSION_CONFLICT"


def test_config_validation_injects_nested_defaults_without_mutating_input():
    registry = AgentRegistry()
    registry.register(descriptor(), Factory())
    original = {}

    first = registry.validate_config("com.example.agent", original)
    second = registry.validate_config("com.example.agent", first.normalized_config)

    assert first.valid is True
    assert original == {}
    assert dict(first.normalized_config) == {"max_steps": 5, "profile": first.normalized_config["profile"]}
    assert dict(first.normalized_config["profile"]) == {"mode": "safe"}
    assert first.normalized_config == second.normalized_config


def test_bad_config_and_bad_schema_have_stable_errors():
    registry = AgentRegistry()
    registry.register(descriptor(), Factory())

    report = registry.validate_config("com.example.agent", {"max_steps": 0, "unknown": True})
    assert report.valid is False
    assert [(issue.path, issue.validator) for issue in report.errors] == [
        ("", "additionalProperties"),
        ("max_steps", "minimum"),
    ]

    with pytest.raises(ComponentConfigError) as caught:
        registry.register(
            AgentDescriptor(
                "com.example.invalid",
                "1.0.0",
                {"type": "not-a-json-schema-type"},
            ),
            Factory(),
        )
    assert caught.value.code == "AGENT_CONFIG_SCHEMA_INVALID"


def test_freeze_blocks_all_writes_but_allows_concurrent_reads():
    registry = AgentRegistry()
    registry.register(descriptor(), Factory())
    registry.freeze()
    registry.freeze()

    with pytest.raises(AgentRegistryFrozenError):
        registry.register(AgentDescriptor("com.example.other", "1.0.0"), Factory())
    with pytest.raises(AgentRegistryFrozenError):
        registry.unregister("com.example.agent")

    with ThreadPoolExecutor(max_workers=8) as executor:
        versions = list(
            executor.map(
                lambda _: registry.get("com.example.agent").descriptor.version,
                range(100),
            )
        )
    assert versions == ["1.0.0"] * 100


def test_unknown_and_unregister_contract():
    registry = AgentRegistry()
    with pytest.raises(ComponentNotFoundError) as caught:
        registry.get("com.example.missing")
    assert caught.value.code == "AGENT_NOT_FOUND"

    registry.register(descriptor(), Factory())
    registry.unregister("com.example.agent")
    with pytest.raises(ComponentNotFoundError):
        registry.get("com.example.agent")


@pytest.mark.parametrize(
    ("agent_id", "version"),
    [("notnamespaced", "1.0.0"), ("com.example.agent", "v1")],
)
def test_descriptor_rejects_invalid_identity(agent_id, version):
    with pytest.raises(ValueError):
        AgentDescriptor(agent_id, version)
