"""Built-in file-backed Prompt profiles used by bundled Agents."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from benchmark.contracts import (
    ComponentConfigError,
    ExtensionRef,
    PromptProfileDescriptor,
    ValidationIssue,
    ValidationReport,
)
from benchmark.prompts import (
    PromptRegistry,
    PromptResolver,
    PromptSourcePriority,
    load_prompt_directory,
)

BUILTIN_PROMPT_EXTENSION = ExtensionRef("benchmark.core", "1.0.0")
BUILTIN_PROMPT_ROOT = Path(__file__).resolve().parent
BUILTIN_PROMPT_INDEX = BUILTIN_PROMPT_ROOT / "index.yaml"

REACT_PROFILE_BY_FLAGS = MappingProxyType(
    {
        (False, False): "core.react.default",
        (True, False): "core.react.memory",
        (False, True): "core.react.tool-routing",
        (True, True): "core.react.memory-tool-routing",
    }
)


@dataclass(frozen=True)
class PromptProfileContract:
    slots: Mapping[str, frozenset[str]]


def _contract(**slots: tuple[str, ...]) -> PromptProfileContract:
    return PromptProfileContract(
        MappingProxyType(
            {slot: frozenset(variables) for slot, variables in slots.items()}
        )
    )


PROFILE_CONTRACTS = MappingProxyType(
    {
        "react": _contract(
            system=(
                "simulation_notice_block",
                "tool_routing_block",
                "workflow_core_block",
                "workflow_memory_block",
                "available_tools_block",
                "memory_system_block",
                "memory_checklist_block",
                "runtime_protocol_block",
                "final_output_block",
            ),
            runtime_system=("system_prompt", "current_time"),
            simulation_notice=(),
            routing=(),
            workflow_core=(),
            workflow_memory=(),
            available_tools=(),
            memory_system=(),
            memory_checklist=(),
            runtime_protocol=(),
            final_output=(),
            step_reminder=("remaining_steps", "termination_token"),
        ),
        "multi_agent": _contract(
            manager=("context", "portfolio", "prices"),
            trading=("instruction", "portfolio", "prices"),
            news=("instruction",),
            coder=("instruction",),
        ),
        "advanced_multi_agent": _contract(
            manager=(
                "objective",
                "portfolio",
                "prices",
                "evidence_book",
                "context",
                "conflicts",
                "collaboration_state",
            ),
            trading=("instruction", "portfolio", "prices"),
            news=("instruction",),
            coder=("instruction",),
            analyst=("instruction", "portfolio", "prices"),
            critic=("instruction", "portfolio", "prices"),
            execution=(),
        ),
        "rule_aware": _contract(
            system=("rule_documents", "current_time", "portfolio", "prices"),
            reminder=("remaining_steps",),
        ),
        "search_sub_agent": _contract(
            system=("max_steps",),
        ),
        "compliance_audit": _contract(
            system=(),
        ),
    }
)


@lru_cache(maxsize=1)
def get_builtin_prompt_registry() -> PromptRegistry:
    loaded = load_prompt_directory(BUILTIN_PROMPT_ROOT, BUILTIN_PROMPT_INDEX)
    registry = PromptRegistry()
    registry.register_provider(
        BUILTIN_PROMPT_EXTENSION,
        loaded,
        PromptSourcePriority.BUILTIN,
    )
    registry.register_profiles(
        BUILTIN_PROMPT_EXTENSION,
        loaded.profiles,
        PromptSourcePriority.BUILTIN,
    )
    registry.freeze()
    return registry


def get_prompt_resolver(prompts: PromptResolver | None) -> PromptResolver:
    return prompts if prompts is not None else get_builtin_prompt_registry()


def read_builtin_template(relative_path: str) -> str:
    return (BUILTIN_PROMPT_ROOT / relative_path).read_text(encoding="utf-8")


def render_template_source(prompt_id: str) -> str:
    resolver = get_builtin_prompt_registry()
    spec = resolver.get_prompt_spec(prompt_id)
    variables = {
        name: "{" + name + "}"
        for name in set(spec.required_variables).union(spec.optional_variables)
    }
    return resolver.render(prompt_id, variables).content


def render_react_prompt(
    *,
    memory_enabled: bool,
    tool_routing_enabled: bool,
    include_simulation_notice: bool,
    resolver: PromptResolver | None = None,
) -> str:
    prompts = get_prompt_resolver(resolver)
    profile_id = REACT_PROFILE_BY_FLAGS[(memory_enabled, tool_routing_enabled)]
    require_profile_contract(prompts, profile_id, "react")
    block_slots = (
        "routing",
        "workflow_core",
        "workflow_memory",
        "available_tools",
        "memory_system",
        "memory_checklist",
        "runtime_protocol",
        "final_output",
    )
    blocks = {
        f"{slot}_block": prompts.render_slot(profile_id, slot, {}).content.strip()
        for slot in block_slots
    }
    blocks["tool_routing_block"] = blocks.pop("routing_block")
    blocks["simulation_notice_block"] = (
        prompts.render_slot(profile_id, "simulation_notice", {}).content.strip()
        if include_simulation_notice
        else ""
    )
    return prompts.render_slot(profile_id, "system", blocks).content.strip()


def validate_profile_contract(
    resolver: PromptResolver,
    profile_id: str,
    contract_name: str,
) -> ValidationReport:
    contract = PROFILE_CONTRACTS[contract_name]
    errors: list[ValidationIssue] = []
    try:
        profile = resolver.get_profile(profile_id)
    except Exception as exc:
        return ValidationReport(
            False,
            errors=(
                ValidationIssue(
                    path="profile_id",
                    message=str(exc),
                    code="PROMPT_PROFILE_NOT_FOUND",
                ),
            ),
        )
    for slot, expected_variables in contract.slots.items():
        selection = profile.slots.get(slot)
        if selection is None:
            errors.append(
                ValidationIssue(
                    path=f"slots.{slot}",
                    message="required Prompt profile slot is missing",
                    code="PROMPT_PROFILE_SLOT_MISSING",
                )
            )
            continue
        try:
            spec = resolver.get_prompt_spec(
                selection.prompt_id,
                version=selection.version,
            )
        except Exception as exc:
            errors.append(
                ValidationIssue(
                    path=f"slots.{slot}",
                    message=str(exc),
                    code="PROMPT_PROFILE_BINDING_INVALID",
                )
            )
            continue
        declared = set(spec.required_variables).union(spec.optional_variables)
        if declared != set(expected_variables):
            errors.append(
                ValidationIssue(
                    path=f"slots.{slot}",
                    message="Prompt variables do not match the built-in slot contract",
                    code="PROMPT_PROFILE_SLOT_VARIABLES_INVALID",
                )
            )
    return ValidationReport(not errors, errors=tuple(errors))


def require_profile_contract(
    resolver: PromptResolver,
    profile_id: str,
    contract_name: str,
) -> PromptProfileDescriptor:
    report = validate_profile_contract(resolver, profile_id, contract_name)
    if not report.valid:
        raise ComponentConfigError(
            "Prompt profile is incompatible with the built-in Agent",
            code="PROMPT_PROFILE_INCOMPATIBLE",
            details={
                "profile_id": profile_id,
                "errors": [
                    {"path": issue.path, "code": issue.code, "message": issue.message}
                    for issue in report.errors
                ],
            },
        )
    return resolver.get_profile(profile_id)


__all__ = [
    "BUILTIN_PROMPT_EXTENSION",
    "BUILTIN_PROMPT_INDEX",
    "BUILTIN_PROMPT_ROOT",
    "PROFILE_CONTRACTS",
    "REACT_PROFILE_BY_FLAGS",
    "PromptProfileContract",
    "get_builtin_prompt_registry",
    "get_prompt_resolver",
    "read_builtin_template",
    "render_react_prompt",
    "render_template_source",
    "require_profile_contract",
    "validate_profile_contract",
]
