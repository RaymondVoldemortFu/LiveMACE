"""Load rule JSON independently of Agent template modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RuleCatalog:
    def __init__(self, rules_dir: Path | None = None) -> None:
        self._rules_dir = rules_dir or Path(__file__).resolve().parents[3] / "config" / "rules"

    def load(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "r0_rules": self._read("r0_system_hard.json"),
            "r1_rules": self._read("r1_client_hard.json"),
            "r2_rules": self._read("r2_client_soft.json"),
        }

    def summary(self) -> dict[str, Any]:
        rules = self.load()
        r0_count = len(rules["r0_rules"])
        r1_count = len(rules["r1_rules"])
        r2_count = len(rules["r2_rules"])
        return {
            "total_rules": r0_count + r1_count + r2_count,
            "r0_count": r0_count,
            "r1_count": r1_count,
            "r2_count": r2_count,
            "categories": {
                "r0": {
                    "name": "System Hard Constraints",
                    "description": "Critical system-level rules that cannot be violated",
                    "count": r0_count,
                },
                "r1": {
                    "name": "Client Hard Rules",
                    "description": "Client-specified mandatory requirements",
                    "count": r1_count,
                },
                "r2": {
                    "name": "Client Soft Preferences",
                    "description": "Client preferences with weighted scoring",
                    "count": r2_count,
                },
            },
        }

    def list_rules(self) -> dict[str, Any]:
        rules = self.load()
        result: list[dict[str, Any]] = []
        for rule in rules["r0_rules"]:
            result.append(
                {
                    "id": rule["id"],
                    "name": rule["name"],
                    "category": "R0",
                    "category_name": "System Hard",
                }
            )
        for rule in rules["r1_rules"]:
            result.append(
                {
                    "id": rule["id"],
                    "name": rule["name"],
                    "category": "R1",
                    "category_name": "Client Hard",
                }
            )
        for rule in rules["r2_rules"]:
            result.append(
                {
                    "id": rule["id"],
                    "name": rule["name"],
                    "category": "R2",
                    "category_name": "Client Soft",
                    "weight": rule.get("weight", 1.0),
                }
            )
        return {"total": len(result), "rules": result}

    def _read(self, filename: str) -> list[dict[str, Any]]:
        path = self._rules_dir / filename
        if not path.is_file():
            return []
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        rules = payload.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError(f"{filename} rules must be a list")
        return rules
