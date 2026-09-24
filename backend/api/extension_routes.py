"""Thin M14 HTTP boundary for extensions and account runtime configuration."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from schemas.extensions import (
    ComponentOut,
    ComponentSchemaOut,
    ExtensionOut,
    PromptProfileOut,
    ToolOut,
    ToolsetOut,
    RuntimeConfigOut,
    RuntimeConfigSaveRequest,
    RuntimeConfigValidateRequest,
    RuntimeConfigValidationOut,
)
from services.extension_config_service import (
    ExtensionConfigService,
    ExtensionConfigServiceError,
    get_extension_config_service,
)

router = APIRouter(tags=["extensions"])


def _error(exc: ExtensionConfigServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": str(uuid4()),
            }
        },
    )


@router.get("/api/extensions", response_model=list[ExtensionOut])
def list_extensions(service: ExtensionConfigService = Depends(get_extension_config_service)):
    return service.list_extensions()


@router.get("/api/extensions/agents", response_model=list[ComponentOut])
def list_agents(service: ExtensionConfigService = Depends(get_extension_config_service)):
    return service.list_agents()


@router.get("/api/extensions/toolsets", response_model=list[ToolsetOut])
def list_toolsets(service: ExtensionConfigService = Depends(get_extension_config_service)):
    return service.list_toolsets()


@router.get("/api/extensions/tools", response_model=list[ToolOut])
def list_tools(service: ExtensionConfigService = Depends(get_extension_config_service)):
    return service.list_tools()


@router.get("/api/extensions/prompts", response_model=list[PromptProfileOut])
def list_prompts(service: ExtensionConfigService = Depends(get_extension_config_service)):
    return service.list_prompts()


@router.get(
    "/api/extensions/components/{component_id}/schema",
    response_model=ComponentSchemaOut,
    response_model_by_alias=True,
)
def component_schema(
    component_id: str,
    service: ExtensionConfigService = Depends(get_extension_config_service),
):
    try:
        return service.component_schema(component_id)
    except ExtensionConfigServiceError as exc:
        return _error(exc)


@router.get("/api/account/{account_id}/runtime-config", response_model=RuntimeConfigOut)
def get_runtime_config(
    account_id: int,
    service: ExtensionConfigService = Depends(get_extension_config_service),
):
    try:
        return service.get_runtime_config(account_id)
    except ExtensionConfigServiceError as exc:
        return _error(exc)


@router.post(
    "/api/account/{account_id}/runtime-config/validate",
    response_model=RuntimeConfigValidationOut,
)
def validate_runtime_config(
    account_id: int,
    request: RuntimeConfigValidateRequest,
    service: ExtensionConfigService = Depends(get_extension_config_service),
):
    del account_id
    return service.validate(request.config.model_dump())


@router.put("/api/account/{account_id}/runtime-config", response_model=RuntimeConfigOut)
def save_account_runtime_config(
    account_id: int,
    request: RuntimeConfigSaveRequest,
    service: ExtensionConfigService = Depends(get_extension_config_service),
):
    try:
        return service.save(
            account_id,
            request.config.model_dump(),
            request.expected_updated_at,
        )
    except ExtensionConfigServiceError as exc:
        return _error(exc)
