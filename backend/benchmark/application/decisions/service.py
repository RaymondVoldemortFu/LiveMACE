"""Synchronous decision-round orchestration facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping


@dataclass(frozen=True)
class RunDecisionRound:
    account_ids: tuple[int, ...] | None
    max_concurrency: int
    trigger: str

    def __post_init__(self) -> None:
        if self.account_ids is not None and not all(isinstance(item, int) and item > 0 for item in self.account_ids):
            raise ValueError("account_ids must be positive integers")
        if not isinstance(self.max_concurrency, int) or self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be a positive integer")
        if not isinstance(self.trigger, str) or not self.trigger.strip():
            raise ValueError("trigger must be a non-empty string")


@dataclass(frozen=True)
class DecisionRoundResult:
    decision_round_id: str | None
    processed_accounts: int
    errors: Mapping[int, str]


@dataclass(frozen=True)
class DecisionRoundService:
    """Facade preserving the current synchronous scheduler entrypoint."""

    entrypoint: Callable[[], None] | None = None

    def run(self, request: RunDecisionRound) -> DecisionRoundResult:
        entrypoint = self.entrypoint
        if entrypoint is None:
            from services.trading_commands import place_ai_driven_crypto_order

            entrypoint = place_ai_driven_crypto_order
        entrypoint()
        return DecisionRoundResult(decision_round_id=None, processed_accounts=0, errors={})


__all__ = ["DecisionRoundResult", "DecisionRoundService", "RunDecisionRound"]