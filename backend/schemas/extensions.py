"""Public DTOs for extension discovery and per-account runtime configuration."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ValidationIssueOut(BaseModel):
    path: str
    message: str
    code: Optional[str] = None
    validator: Optional[str] = None


class ExtensionOut(BaseModel):
    id: Optional[str]
    version: Optional[str]
    name: Optional[str]
    description: str = ""
    source: str
    status: str
    requested_capabilities: List[str] = Field(default_factory=list)
    allowed_capabilities: List[str] = Field(default_factory=list)
    errors: List[ValidationIssueOut] = Field(default_factory=list)


class ComponentOut(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    source: str
    status: str = "loaded"
    config_schema: Dict[str, Any] = Field(default_factory=dict)
    requested_capabilities: List[str] = Field(default_factory=list)
    allowed_capabilities: List[str] = Field(default_factory=list)


class PromptProfileOut(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    source: str
    status: str = "loaded"
    slots: Dict[str, Dict[str, Optional[str]]] = Field(default_factory=dict)
    requested_capabilities: List[str] = Field(default_factory=list)
    allowed_capabilities: List[str] = Field(default_factory=list)


class ComponentSchemaOut(BaseModel):
    component_id: str
    component_type: str
    version: str
    schema_: Dict[str, Any] = Field(alias="schema")

    class Config:
        populate_by_name = True


class RuntimeConfigBody(BaseModel):
    agent_id: str
    agent_version: Optional[str] = None
    agent_config: Dict[str, Any] = Field(default_factory=dict)
    toolset_ids: List[str] = Field(default_factory=list)
    disabled_tools: List[str] = Field(default_factory=list)
    prompt_profile_id: Optional[str] = None
    prompt_profile_version: Optional[str] = None
    component_versions: Dict[str, str] = Field(default_factory=dict)


class RuntimeConfigValidateRequest(BaseModel):
    config: RuntimeConfigBody


class RuntimeConfigSaveRequest(BaseModel):
    config: RuntimeConfigBody
    expected_updated_at: Optional[str] = None


class RuntimeConfigValidationOut(BaseModel):
    valid: bool
    status: str
    config: Optional[RuntimeConfigBody] = None
    errors: List[ValidationIssueOut] = Field(default_factory=list)
    warnings: List[ValidationIssueOut] = Field(default_factory=list)


class RuntimeConfigOut(BaseModel):
    account_id: int
    config: RuntimeConfigBody
    validation_status: str
    validation_errors: List[ValidationIssueOut] = Field(default_factory=list)
    updated_at: Optional[str] = None
