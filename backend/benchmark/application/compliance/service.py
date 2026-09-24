"""Compliance reads and one-trace evaluation DTO."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel
from benchmark.persistence.repositories import (
    AccountRepository,
    RuleEvaluationRepository,
    TraceRepository,
    DecisionRepository,
)
from benchmark.application.compliance.reads import ComplianceReadService


class ComplianceResultDTO(BaseModel):
    account_id: int | None
    account_name: str | None
    trace_id: str | None
    decision_round_id: str | None
    component_versions: dict[str, str | None]
    gate_pass: bool | None
    s_rule_sat: float | None
    s_audit: float | None
    final_score: float | None


@dataclass(frozen=True)
class ComplianceRequest:
    account_id: int
    trace_id: str | None = None


class ComplianceService(ComplianceReadService):
    def __init__(
        self,
        accounts: AccountRepository,
        rules: RuleEvaluationRepository,
        traces: TraceRepository,
        decisions: DecisionRepository,
    ):
        super().__init__(rules, decisions)
        self._accounts, self._rules, self._traces = accounts, rules, traces

    def evaluate(self, request: ComplianceRequest) -> ComplianceResultDTO:
        account = self._accounts.get(request.account_id)
        row = self._rules.latest(request.account_id, request.trace_id)
        trace_id = (
            row.trace_id if row is not None and row.trace_id else request.trace_id
        )
        events = self._traces.list_runtime_events(trace_id) if trace_id else []
        decision_round_id = next(
            (
                event.decision_round_id
                for event in events
                if event.account_id == request.account_id and event.decision_round_id
            ),
            None,
        )
        versions = _component_versions(events, request.account_id)
        return ComplianceResultDTO(
            account_id=account.id if account is not None else None,
            account_name=account.name if account is not None else None,
            trace_id=trace_id,
            decision_round_id=decision_round_id,
            component_versions=versions,
            gate_pass=None if row is None else row.gate_pass == "true",
            s_rule_sat=None
            if row is None or row.s_rule_sat is None
            else float(row.s_rule_sat),
            s_audit=None if row is None or row.s_audit is None else float(row.s_audit),
            final_score=None
            if row is None or row.final_score is None
            else float(row.final_score),
        )


def _component_versions(events, account_id: int) -> dict[str, str | None]:
    for event in events:
        if event.account_id != account_id or event.event_type != "run.configured":
            continue
        try:
            payload = (
                json.loads(event.payload)
                if isinstance(event.payload, str)
                else event.payload
            )
        except json.JSONDecodeError:
            return {}
        raw = payload.get("component_versions") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): value if isinstance(value, str) and value else None
            for key, value in raw.items()
        }
    return {}
