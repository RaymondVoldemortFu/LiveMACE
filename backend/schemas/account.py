from typing import Any, Dict, List, Optional

from pydantic import AwareDatetime, BaseModel, Field


class AccountCreate(BaseModel):
    """Create a new AI Trading Account"""
    name: str  # Display name (e.g., "GPT Trader", "Claude Analyst")
    model: str
    base_url: str
    api_key: str
    initial_capital: float = 10000.0
    account_type: str = "AI"  # "AI" or "MANUAL"
    agent_type: str = "react"


class AccountUpdate(BaseModel):
    """Update AI Trading Account"""
    name: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    agent_type: Optional[str] = None


class AccountOut(BaseModel):
    """AI Trading Account output"""
    id: int
    user_id: int
    name: str
    model: str
    base_url: str
    api_key: str  # Will be masked in API responses
    initial_capital: float
    current_cash: float
    frozen_cash: float
    account_type: str
    agent_type: str
    is_active: bool

    class Config:
        from_attributes = True


class AccountOverview(BaseModel):
    """Account overview with portfolio information"""
    account: AccountOut
    total_assets: float  # Total assets in USD
    positions_value: float  # Total positions value in USD


class AccountExtensionConfigDTO(BaseModel):
    """Typed account extension configuration (M12, spec §9).

    Never carries secrets: API key / model / base_url stay on the account and
    are resolved separately. This is the request/response shape for the
    account runtime-config endpoints.
    """

    agent_id: str
    agent_config: Dict[str, Any] = Field(default_factory=dict)
    toolset_ids: List[str] = Field(default_factory=list)
    disabled_tools: List[str] = Field(default_factory=list)
    prompt_profile_id: Optional[str] = None
    component_versions: Dict[str, str] = Field(default_factory=dict)


class RuntimeConfigValidationIssueDTO(BaseModel):
    """One validation diagnostic for an account runtime config."""

    path: str
    message: str
    validator: str


class AccountRuntimeConfigOut(BaseModel):
    """Account runtime config plus its validation status for API responses."""

    account_id: int
    config: AccountExtensionConfigDTO
    validation_status: str  # "valid" | "configuration_invalid"
    validation_errors: List[RuntimeConfigValidationIssueDTO] = Field(
        default_factory=list
    )
    updated_at: AwareDatetime


class AccountRuntimeConfigSave(BaseModel):
    """Request body to save an account runtime config with optimistic locking."""

    config: AccountExtensionConfigDTO
    expected_updated_at: Optional[AwareDatetime] = None
