from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from benchmark.contracts import (
    ComponentConfigError,
    ComponentConflictError,
    ExtensionRef,
    PromptProfileDescriptor,
    PromptSelection,
    PromptSpec,
)
from benchmark.prompts import (
    PromptRegistry,
    PromptRegistryFrozenError,
    PromptSourcePriority,
)
from benchmark.prompts.renderer import parse_template, render_template


class Provider:
    def __init__(self, prompt_id: str, version: str, content: str):
        self.spec = PromptSpec(prompt_id, version, ())
        self.template = parse_template(content)

    def list_prompts(self):
        return (self.spec,)

    def render(self, prompt_id, variables):
        assert prompt_id == self.spec.id
        return render_template(self.template, self.spec, variables)


def ref(name: str, version: str = "1.0.0") -> ExtensionRef:
    return ExtensionRef(f"com.example.{name}", version)


def test_priority_precedes_semver_and_exact_version_is_respected():
    registry = PromptRegistry()
    registry.register_provider(
        ref("builtin", "2.0.0"),
        Provider("core.react.system", "2.0.0", "builtin"),
        PromptSourcePriority.BUILTIN,
    )
    registry.register_provider(
        ref("external"),
        Provider("core.react.system", "1.0.0", "external"),
        PromptSourcePriority.EXTERNAL,
    )

    assert registry.resolve("core.react.system").spec.version == "1.0.0"
    assert registry.render("core.react.system", {}).content == "external"
    assert registry.resolve("core.react.system", "2.0.0").spec.version == "2.0.0"


def test_same_id_version_priority_conflicts_atomically():
    registry = PromptRegistry()
    registry.register_provider(
        ref("first"), Provider("core.react.system", "1.0.0", "a"), 100
    )

    with pytest.raises(ComponentConflictError) as caught:
        registry.register_provider(
            ref("second"), Provider("core.react.system", "1.0.0", "b"), 100
        )

    assert caught.value.code == "PROMPT_VERSION_PRIORITY_CONFLICT"
    assert registry.render("core.react.system", {}).content == "a"


def test_freeze_validates_profile_bindings_before_becoming_immutable():
    registry = PromptRegistry()
    profile = PromptProfileDescriptor(
        "core.react.default",
        "1.0.0",
        {"system": PromptSelection("core.react.system", "1.0.0")},
    )
    registry.register_profiles(
        ref("profiles"), (profile,), PromptSourcePriority.BUILTIN
    )

    with pytest.raises(ComponentConfigError) as caught:
        registry.freeze()
    assert caught.value.code == "PROMPT_PROFILE_UNRESOLVED"
    assert registry.frozen is False

    registry.register_provider(
        ref("prompts"),
        Provider("core.react.system", "1.0.0", "ready"),
        PromptSourcePriority.BUILTIN,
    )
    registry.freeze()
    assert registry.frozen is True

    with pytest.raises(PromptRegistryFrozenError):
        registry.register_provider(
            ref("late"), Provider("core.other.system", "1.0.0", "x"), 0
        )


def test_frozen_registry_supports_concurrent_reads():
    registry = PromptRegistry()
    registry.register_provider(
        ref("prompts"), Provider("core.react.system", "1.0.0", "stable"), 0
    )
    registry.freeze()

    with ThreadPoolExecutor(max_workers=8) as executor:
        contents = list(
            executor.map(
                lambda _: registry.render("core.react.system", {}).content, range(100)
            )
        )

    assert contents == ["stable"] * 100


def test_frozen_registry_rejects_provider_without_calling_it():
    class ExplodingProvider:
        def list_prompts(self):
            raise AssertionError("frozen registry must not call providers")

        def render(self, prompt_id, variables):
            raise AssertionError("not reached")

    registry = PromptRegistry()
    registry.register_provider(
        ref("prompts"), Provider("core.react.system", "1.0.0", "stable"), 0
    )
    registry.freeze()

    with pytest.raises(PromptRegistryFrozenError):
        registry.register_provider(ref("late"), ExplodingProvider(), 0)
