"""Synchronous decision-round orchestration facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class RunDecisionRound:
    account_ids: tuple[int, ...] | None
    max_concurrency: int
    trigger: str

    def __post_init__(self) -> None:
        if self.account_ids is not None and not all(
            isinstance(item, int) and item > 0 for item in self.account_ids
        ):
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


class DecisionRoundService:
    """Coordinate immutable worker inputs and synchronous per-account runtimes."""

    def __init__(
        self,
        entrypoint=None,
        *,
        selector=None,
        prices=None,
        worker=None,
        is_cancelled=None,
        lock=None,
    ):
        self.entrypoint = entrypoint
        self.selector = selector
        self.prices = prices
        self.worker = worker
        self.is_cancelled = is_cancelled
        self.lock = lock

    def run(self, request: RunDecisionRound) -> DecisionRoundResult:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from uuid import uuid4
        from services import trading_commands as commands
        from database.connection import SessionLocal
        from .selection import select_agent_accounts
        from .runner import run_account

        if self.entrypoint is not None:
            return self.entrypoint(request)
        lock = self.lock or commands._ai_trade_run_lock
        if not lock.acquire(blocking=False):
            return DecisionRoundResult(None, 0, {})
        round_id = str(uuid4())
        errors = {}
        processed = 0
        cancelled = self.is_cancelled or commands._shutdown_requested
        try:
            if cancelled():
                return DecisionRoundResult(round_id, 0, {})
            if self.selector:
                account_ids = self.selector(request.account_ids)
            else:
                with SessionLocal() as db:
                    account_ids = [
                        a.id for a in select_agent_accounts(db, request.account_ids)
                    ]
            if not account_ids:
                return DecisionRoundResult(round_id, 0, {})
            prices = (
                self.prices()
                if self.prices
                else {
                    **commands._get_market_prices(
                        commands.AI_TRADING_SYMBOLS, "CRYPTO"
                    ),
                    **(
                        commands._get_market_prices(commands.US_TRADING_SYMBOLS, "US")
                        if commands._baseline_should_fetch_us_prices()
                        else {}
                    ),
                }
            )
            if not prices:
                return DecisionRoundResult(
                    round_id, 0, {i: "Market prices unavailable" for i in account_ids}
                )
            worker = self.worker or run_account
            with ThreadPoolExecutor(
                max_workers=min(request.max_concurrency, len(account_ids))
            ) as executor:
                futures = {
                    executor.submit(
                        worker, i, prices, round_id, is_cancelled=cancelled
                    ): i
                    for i in dict.fromkeys(account_ids)
                }
                for future in as_completed(futures):
                    if cancelled():
                        for pending in futures:
                            pending.cancel()
                    try:
                        result = future.result()
                        processed += 1
                        if result.termination_reason.value in {
                            "llm_error",
                            "tool_error",
                            "cancelled",
                            "max_steps",
                        }:
                            errors[futures[future]] = result.termination_reason.value
                    except Exception as exc:
                        errors[futures[future]] = getattr(
                            exc, "code", type(exc).__name__
                        )
            return DecisionRoundResult(round_id, processed, errors)
        finally:
            lock.release()


__all__ = ["DecisionRoundResult", "DecisionRoundService", "RunDecisionRound"]
