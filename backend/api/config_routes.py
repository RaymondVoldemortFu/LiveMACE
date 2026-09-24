"""System configuration HTTP boundary."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/config", tags=["config"])


class RequiredConfigStatus(BaseModel):
    has_required_configs: bool
    missing_configs: list[str] = Field(default_factory=list)


@router.get("/check-required", response_model=RequiredConfigStatus)
def check_required_configs():
    return RequiredConfigStatus(has_required_configs=True)
