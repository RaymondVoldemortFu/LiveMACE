"""Provider health DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from benchmark.contracts import JsonValue
from benchmark.contracts.common import _freeze_mapping, _require_non_empty


@dataclass(frozen=True)
class HealthStatus:
    status: str
    provider_id: str
    message: str = ""
    details: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"ok", "degraded", "unavailable"}:
            raise ValueError("status must be ok, degraded, or unavailable")
        _require_non_empty(self.provider_id, "provider_id")
        if not isinstance(self.message, str):
            raise TypeError("message must be str")
        object.__setattr__(self, "details", _freeze_mapping(self.details, "details"))


__all__ = ["HealthStatus"]
