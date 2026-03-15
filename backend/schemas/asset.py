"""Asset metadata-related Pydantic schemas for API validation"""

from pydantic import BaseModel
from typing import Optional


class AssetMetadataOut(BaseModel):
    """Asset metadata output"""
    symbol: str
    sector: str
    is_meme: str  # "true" or "false"
    market_cap_usd: Optional[float] = None
    instrument_type: Optional[str] = None
    liquidity_score: Optional[float] = None

    class Config:
        from_attributes = True


class AssetMetadataCreate(BaseModel):
    """Create or update asset metadata"""
    symbol: str
    sector: str
    is_meme: str = "false"
    market_cap_usd: Optional[float] = None
    instrument_type: Optional[str] = None
    liquidity_score: Optional[float] = None


class AssetMetadataUpdate(BaseModel):
    """Update asset metadata (all fields optional)"""
    sector: Optional[str] = None
    is_meme: Optional[str] = None
    market_cap_usd: Optional[float] = None
    instrument_type: Optional[str] = None
    liquidity_score: Optional[float] = None


class AssetBlacklistRequest(BaseModel):
    """Add/remove asset from blacklist"""
    symbols: list[str]
    action: str  # "add" or "remove"
    reason: Optional[str] = None
