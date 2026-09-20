"""Read-only Catalog facade over one loaded extension runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from benchmark.contracts import (
    KNOWN_CAPABILITIES,
    BenchmarkError,
    PromptProfileDescriptor,
    ToolSpec,
    ValidationIssue,
    ValidationReport,
)

from .loader import ExtensionLoadRecord, ExtensionLoadResult
from .runtime_config import AccountRuntimeConfigDTO


@dataclass(frozen=True)
class ExtensionCatalog:
    """Stable read-only view of loaded components and extension health."""

    _result: ExtensionLoadResult
    _allowed_capabilities: frozenset[str]

    def __init__(
        self,
        result: ExtensionLoadResult,
        *,
        allowed_capabilities: frozenset[str] = frozenset(KNOWN_CAPABILITIES),
    ) -> None:
        if not isinstance(result, ExtensionLoadResult):
            raise TypeError("result must be ExtensionLoadResult")
        capabilities = frozenset(allowed_capabilities)
        unknown = capabilities.difference(KNOWN_CAPABILITIES)
        if unknown:
            raise ValueError(
                "allowed_capabilities contains unknown values: "
                + ", ".join(sorted(unknown))
            )
        object.__setattr__(self, "_result", result)
        object.__setattr__(self, "_allowed_capabilities", capabilities)

    def list_extensions(self) -> tuple[ExtensionLoadRecord, ...]:
        return self._result.records

    @property
    def allowed_capabilities(self) -> frozenset[str]:
        """Capabilities the host may grant, without exposing loader internals."""

        return self._allowed_capabilities

    def list_agents(self) -> tuple:
        return self._result.agents.list()

    def list_tools(self) -> tuple[ToolSpec, ...]:
        return self._result.tools.list()

    def list_prompt_profiles(self) -> tuple[PromptProfileDescriptor, ...]:
        return self._result.prompts.list_profiles()

    def validate_account_config(
        self,
        config: AccountRuntimeConfigDTO | Mapping[str, Any],
    ) -> ValidationReport:
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        try:
            dto = (
                config
                if isinstance(config, AccountRuntimeConfigDTO)
                else AccountRuntimeConfigDTO.from_mapping(config)
            )
        except (TypeError, ValueError) as exc:
            return ValidationReport(
                False,
                errors=(
                    ValidationIssue(
                        path="config",
                        message=str(exc),
                        code="ACCOUNT_CONFIG_INVALID",
                    ),
                ),
            )

        selected_agent_version = self._selected_version(
            dto,
            component_id=dto.agent_id,
            explicit_version=dto.agent_version,
            explicit_field="agent_version",
            errors=errors,
        )
        try:
            registered = self._result.agents.get(
                dto.agent_id,
                selected_agent_version,
            )
        except BenchmarkError as exc:
            errors.append(
                ValidationIssue(
                    path="agent_id",
                    message=exc.message,
                    code=exc.code,
                )
            )
            registered = None

        normalized_agent_config: Mapping[str, Any] = dto.agent_config
        if registered is not None:
            report = self._result.agents.validate_config(
                dto.agent_id,
                dto.agent_config,
                selected_agent_version,
            )
            errors.extend(
                ValidationIssue(
                    path=f"agent_config.{issue.path}" if issue.path else "agent_config",
                    message=issue.message,
                    validator=issue.validator,
                    code=issue.code or issue.validator or "AGENT_CONFIG_INVALID",
                )
                for issue in report.errors
            )
            normalized_agent_config = report.normalized_config

        resolved_agent_version = (
            registered.descriptor.version
            if registered is not None
            else selected_agent_version
        )
        if dto.prompt_profile_id is not None:
            selected_profile_version = self._selected_version(
                dto,
                component_id=dto.prompt_profile_id,
                explicit_version=dto.prompt_profile_version,
                explicit_field="prompt_profile_version",
                errors=errors,
            )
            try:
                profile = self._result.prompts.resolve_profile(
                    dto.prompt_profile_id,
                    selected_profile_version,
                )
                resolved_profile_version = profile.descriptor.version
            except BenchmarkError as exc:
                errors.append(
                    ValidationIssue(
                        path="prompt_profile_id",
                        message=exc.message,
                        code=exc.code,
                    )
                )
                resolved_profile_version = selected_profile_version
        else:
            resolved_profile_version = dto.prompt_profile_version
            if resolved_profile_version is not None:
                errors.append(
                    ValidationIssue(
                        path="prompt_profile_version",
                        message="profile version requires prompt_profile_id",
                        code="PROMPT_PROFILE_ID_REQUIRED",
                    )
                )

        families = {"core.react": "react", "core.multi-agent": "multi_agent",
                    "core.advanced-multi-agent": "advanced_multi_agent", "core.rule-aware": "rule_aware"}
        if dto.prompt_profile_id and dto.agent_id in families and resolved_profile_version:
            from types import SimpleNamespace
            from benchmark.builtin.prompts import validate_profile_contract
            registry = self._result.prompts
            resolver = SimpleNamespace(
                get_profile=lambda profile_id: registry.get_profile(profile_id, version=resolved_profile_version),
                get_prompt_spec=registry.get_prompt_spec)
            profile_report = validate_profile_contract(resolver, dto.prompt_profile_id, families[dto.agent_id])
            errors.extend(profile_report.errors)

        disabled = set(dto.disabled_tools)
        for tool_name in dto.disabled_tools:
            try:
                self._result.tools.get(tool_name)
            except BenchmarkError as exc:
                errors.append(
                    ValidationIssue(
                        path="disabled_tools",
                        message=exc.message,
                        code=exc.code,
                    )
                )

        for spec in self._result.tools.list():
            if spec.name in disabled:
                continue
            missing = sorted(
                set(spec.required_capabilities).difference(self._allowed_capabilities)
            )
            if missing:
                errors.append(
                    ValidationIssue(
                        path=f"tools.{spec.name}",
                        message="required capabilities are not granted: " + ", ".join(missing),
                        code="TOOL_CAPABILITY_DENIED",
                    )
                )

        if dto.toolset_ids:
            errors.append(
                ValidationIssue(path="toolset_ids", message="Named toolsets are not available; use disabled_tools to select tools", code="TOOLSET_NOT_FOUND")
            )

        self._validate_component_versions(dto, errors)

        normalized = dto.to_mapping()
        normalized["agent_version"] = resolved_agent_version
        normalized["agent_config"] = dict(normalized_agent_config)
        normalized["prompt_profile_version"] = resolved_profile_version
        return ValidationReport(
            not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
            normalized_config=normalized,
        )

    @staticmethod
    def _selected_version(
        dto: AccountRuntimeConfigDTO,
        *,
        component_id: str,
        explicit_version: str | None,
        explicit_field: str,
        errors: list[ValidationIssue],
    ) -> str | None:
        pinned_version = dto.component_versions.get(component_id)
        if (
            pinned_version is not None
            and explicit_version is not None
            and pinned_version != explicit_version
        ):
            errors.append(
                ValidationIssue(
                    path=f"component_versions.{component_id}",
                    message=f"{explicit_field} and component_versions disagree",
                    code="COMPONENT_VERSION_CONFLICT",
                )
            )
        return explicit_version if explicit_version is not None else pinned_version

    def _validate_component_versions(
        self,
        dto: AccountRuntimeConfigDTO,
        errors: list[ValidationIssue],
    ) -> None:
        available: dict[str, set[str]] = {}
        for descriptor in self._result.agents.list():
            available.setdefault(descriptor.id, set()).add(descriptor.version)
        for profile in self._result.prompts.list_profiles():
            available.setdefault(profile.id, set()).add(profile.version)
        for tool in self._result.tools.list():
            extension_version = self._result.tools.get(tool.name).extension.version
            available.setdefault(tool.name, set()).add(extension_version)

        for component_id, requested_version in dto.component_versions.items():
            loaded_versions = available.get(component_id)
            if loaded_versions is None:
                errors.append(
                    ValidationIssue(
                        path=f"component_versions.{component_id}",
                        message="component is not loaded",
                        code="COMPONENT_NOT_FOUND",
                    )
                )
            elif requested_version not in loaded_versions:
                errors.append(
                    ValidationIssue(
                        path=f"component_versions.{component_id}",
                        message=f"requested version {requested_version!r} is not loaded",
                        code="COMPONENT_VERSION_UNAVAILABLE",
                    )
                )


__all__ = ["ExtensionCatalog"]
