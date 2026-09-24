"""M14 application service for the read-only Catalog and account config API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from benchmark.accounts import (
    AccountExtensionConfig,
    RuntimeConfigConflictError,
    config_from_legacy_account,
    save_runtime_config,
)
from benchmark.accounts.config import mirror_legacy_account_columns
from benchmark.contracts import to_jsonable
from benchmark.extensions import ExtensionRuntime
from benchmark.extensions.host import get_extension_runtime
from benchmark.persistence import SqlAlchemyUnitOfWork
from config.api_feature_config import ApiFeatureConfig
from database.connection import SessionLocal


class ExtensionConfigServiceError(RuntimeError):
    code = "EXTENSION_CONFIG_ERROR"
    status_code = 500

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class AccountNotFoundError(ExtensionConfigServiceError):
    code = "ACCOUNT_NOT_FOUND"
    status_code = 404


class AccountUpdateDisabledError(ExtensionConfigServiceError):
    code = "ACCOUNT_UPDATE_DISABLED"
    status_code = 403


class ComponentNotFoundError(ExtensionConfigServiceError):
    code = "COMPONENT_NOT_FOUND"
    status_code = 404


class RuntimeConfigInvalidError(ExtensionConfigServiceError):
    code = "RUNTIME_CONFIG_INVALID"
    status_code = 422


class RuntimeConfigVersionConflictError(ExtensionConfigServiceError):
    code = "RUNTIME_CONFIG_CONFLICT"
    status_code = 409


def _issue_dict(issue: Any) -> dict[str, Any]:
    return {
        "path": issue.path,
        "message": issue.message,
        "code": getattr(issue, "code", None),
        "validator": getattr(issue, "validator", None),
    }


def _iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return value.isoformat()


@dataclass
class ExtensionConfigService:
    runtime: ExtensionRuntime
    uow_factory: Callable[[], Any]

    @property
    def catalog(self):
        return self.runtime.catalog

    def list_extensions(self) -> list[dict[str, Any]]:
        allowed = sorted(self.catalog.allowed_capabilities)
        return [
            {
                "id": item.id,
                "version": item.version,
                "name": item.name,
                "description": "",
                "source": item.source.value,
                "status": getattr(item.status, "value", item.status),
                "requested_capabilities": list(item.capabilities),
                "allowed_capabilities": [
                    capability for capability in item.capabilities if capability in allowed
                ],
                "errors": [_issue_dict(issue) for issue in item.errors],
            }
            for item in self.catalog.list_extensions()
        ]

    def list_agents(self) -> list[dict[str, Any]]:
        return [
            {
                "id": item.id,
                "name": item.display_name or item.id,
                "version": item.version,
                "description": item.description,
                "source": "builtin" if item.id.startswith(("core.", "baseline.")) else "external",
                "config_schema": to_jsonable(item.config_schema),
            }
            for item in self.catalog.list_agents()
        ]

    def list_toolsets(self) -> list[dict[str, Any]]:
        """Named UI groups expand to the account's existing per-tool opt-outs."""
        groups = {}
        for tool in self.list_tools():
            capabilities = tool['requested_capabilities']
            category = next((name for capability, name in (
                ('trading.write', 'trading'), ('sandbox.write', 'sandbox'),
                ('memory.read', 'memory'), ('memory.write', 'memory'),
                ('network.read', 'research'), ('market.read', 'market'),
                ('account.read', 'account'),
            ) if capability in capabilities), 'other')
            group = groups.setdefault(category, {
                'id': f'core.{category}-tools', 'name': category.title(),
                'version': '1.0.0', 'source': 'catalog', 'status': 'loaded',
                'description': f'{category.title()} tools', 'config_schema': {},
                'requested_capabilities': [], 'allowed_capabilities': [], 'tool_names': [],
            })
            group['tool_names'].append(tool['name'])
            for field in ('requested_capabilities', 'allowed_capabilities'):
                group[field] = sorted(set(group[field]) | set(tool[field]))
        return [groups[key] for key in sorted(groups)]

    def list_tools(self) -> list[dict[str, Any]]:
        allowed = set(self.catalog.allowed_capabilities)
        items: list[dict[str, Any]] = []
        for spec in self.runtime.tools.list():
            entry = self.runtime.tools.get(spec.name)
            extension_id = entry.extension.id
            source = (
                "builtin"
                if extension_id.startswith(("core.", "baseline.", "benchmark."))
                else "external"
            )
            requested = list(spec.required_capabilities)
            items.append(
                {
                    "id": spec.name,
                    "name": spec.name,
                    "version": entry.extension.version,
                    "description": spec.description,
                    "source": source,
                    "status": "loaded",
                    "side_effect": spec.side_effect.value,
                    "requested_capabilities": requested,
                    "allowed_capabilities": [
                        capability for capability in requested if capability in allowed
                    ],
                }
            )
        return items

    def list_prompts(self) -> list[dict[str, Any]]:
        return [
            {
                "id": item.id,
                "name": item.id,
                "version": item.version,
                "source": "builtin" if item.id.startswith("core.") else "external",
                "slots": {
                    name: {"id": selection.prompt_id, "version": selection.version}
                    for name, selection in item.slots.items()
                },
            }
            for item in self.catalog.list_prompt_profiles()
        ]

    def component_schema(self, component_id: str) -> dict[str, Any]:
        for item in self.catalog.list_agents():
            if item.id == component_id:
                return {
                    "component_id": item.id,
                    "component_type": "agent",
                    "version": item.version,
                    "schema": to_jsonable(item.config_schema),
                }
        for item in self.catalog.list_tools():
            if item.name == component_id:
                return {
                    "component_id": item.name,
                    "component_type": "tool",
                    "version": self.runtime.tools.get(item.name).extension.version,
                    "schema": to_jsonable(item.input_schema),
                }
        for item in self.catalog.list_prompt_profiles():
            if item.id == component_id:
                return {
                    "component_id": item.id,
                    "component_type": "prompt_profile",
                    "version": item.version,
                    "schema": {
                        "type": "object",
                        "properties": {name: {"type": "object"} for name in item.slots},
                        "additionalProperties": False,
                    },
                }
        raise ComponentNotFoundError(
            f"component not found: {component_id}",
            details={"component_id": component_id},
        )

    def validate(self, config: Mapping[str, Any]) -> dict[str, Any]:
        report = self.catalog.validate_account_config(config)
        normalized = dict(report.normalized_config) if report.valid else None
        return {
            "valid": report.valid,
            "status": "valid" if report.valid else "configuration_invalid",
            "config": normalized,
            "errors": [_issue_dict(issue) for issue in report.errors],
            "warnings": [_issue_dict(issue) for issue in report.warnings],
        }

    def get_runtime_config(self, account_id: int) -> dict[str, Any]:
        with self.uow_factory() as uow:
            account = uow.accounts.get(account_id)
            if account is None:
                raise AccountNotFoundError(f"account not found: {account_id}")
            row = uow.account_runtime_configs.get(account_id)
            if row is None:
                config = config_from_legacy_account(account)
                return {
                    "account_id": account_id,
                    "config": config.to_dict(),
                    "validation_status": "valid",
                    "validation_errors": [],
                    "updated_at": None,
                }
            return {
                "account_id": account_id,
                "config": {
                    "agent_id": row.agent_id,
                    "agent_version": row.agent_version,
                    "agent_config": row.agent_config_json or {},
                    "toolset_ids": row.toolset_ids_json or [],
                    "disabled_tools": row.disabled_tools_json or [],
                    "prompt_profile_id": row.prompt_profile_id,
                    "prompt_profile_version": row.prompt_profile_version,
                    "component_versions": row.component_versions_json or {},
                },
                "validation_status": row.validation_status,
                "validation_errors": row.validation_errors_json or [],
                "updated_at": _iso(row.updated_at),
            }

    def save(
        self,
        account_id: int,
        config: Mapping[str, Any],
        expected_updated_at: str | None,
    ) -> dict[str, Any]:
        if not ApiFeatureConfig.ENABLE_ACCOUNT_UPDATE_API:
            raise AccountUpdateDisabledError(
                "Account update API is disabled by deployment configuration"
            )
        validation = self.validate(config)
        if not validation["valid"]:
            raise RuntimeConfigInvalidError(
                "runtime configuration is invalid",
                details={"errors": validation["errors"], "warnings": validation["warnings"]},
            )
        normalized = dict(validation["config"])
        versions = dict(normalized.get("component_versions") or {})
        if normalized.get("agent_version"):
            versions[normalized["agent_id"]] = normalized["agent_version"]
        if normalized.get("prompt_profile_id") and normalized.get("prompt_profile_version"):
            versions[normalized["prompt_profile_id"]] = normalized["prompt_profile_version"]
        persisted = AccountExtensionConfig(
            agent_id=normalized["agent_id"],
            agent_config=normalized.get("agent_config") or {},
            toolset_ids=normalized.get("toolset_ids") or (),
            disabled_tools=normalized.get("disabled_tools") or (),
            prompt_profile_id=normalized.get("prompt_profile_id"),
            component_versions=versions,
        )
        try:
            expected = datetime.fromisoformat(expected_updated_at) if expected_updated_at else None
            if expected is not None and expected.tzinfo is None:
                # Older API responses exposed the database's naive-UTC token.
                expected = expected.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise RuntimeConfigInvalidError(
                "expected_updated_at must be an ISO-8601 datetime"
            ) from exc

        with self.uow_factory() as uow:
            account = uow.accounts.get_for_update(account_id)
            if account is None:
                raise AccountNotFoundError(f"account not found: {account_id}")
            try:
                result = save_runtime_config(
                    uow,
                    account_id,
                    persisted,
                    expected_updated_at=expected,
                    agent_registry=self.runtime.agents,
                    prompt_registry=self.runtime.prompts,
                )
            except RuntimeConfigConflictError as exc:
                raise RuntimeConfigVersionConflictError(
                    str(exc),
                    details={
                        "expected_updated_at": _iso(exc.expected_updated_at),
                        "actual_updated_at": _iso(exc.actual_updated_at),
                    },
                ) from exc
            if not result.valid:
                raise RuntimeConfigInvalidError(
                    "runtime configuration is invalid",
                    details={"errors": [_issue_dict(issue) for issue in result.issues]},
                )
            mirror_legacy_account_columns(account, result.config)
            uow.commit()
        return self.get_runtime_config(account_id)


def get_extension_config_service() -> ExtensionConfigService:
    runtime = get_extension_runtime()
    return ExtensionConfigService(
        runtime=runtime,
        uow_factory=lambda: SqlAlchemyUnitOfWork(SessionLocal),
    )


__all__ = [
    "AccountNotFoundError",
    "AccountUpdateDisabledError",
    "ComponentNotFoundError",
    "ExtensionConfigService",
    "ExtensionConfigServiceError",
    "RuntimeConfigInvalidError",
    "RuntimeConfigVersionConflictError",
    "get_extension_config_service",
]
