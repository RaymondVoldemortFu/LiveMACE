#!/usr/bin/env python3
"""Watch the isolated Wave3 stack until three scheduler rounds finish for accounts 1-4."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone


ACCOUNTS = (1, 2, 3, 4)
NEEDED = 3
POLL_SECONDS = 15
LIMIT_SECONDS = 90 * 60
COMPLETE_TERMINATION_REASONS = frozenset({"trade_done", "hold"})


def is_complete_account_result(item: dict) -> bool:
    return (
        item.get("event_type") == "run.result"
        and item.get("termination_reason") in COMPLETE_TERMINATION_REASONS
    )


def is_complete_cycle(accounts: dict, expected=ACCOUNTS) -> bool:
    if set(accounts) != set(expected):
        return False
    return all(is_complete_account_result(item) for item in accounts.values())


def update_consecutive_cycles(cycles: list, accounts: dict, expected=ACCOUNTS) -> list:
    if not is_complete_cycle(accounts, expected=expected):
        return []
    return [*cycles, accounts]


def existing_rounds() -> set[str]:
    from database.connection import SessionLocal
    from database.models import RuntimeEvent

    with SessionLocal() as db:
        rows = (
            db.query(RuntimeEvent.decision_round_id)
            .filter(
                RuntimeEvent.account_id.in_(ACCOUNTS),
                RuntimeEvent.event_type.in_(("run.result", "run.failed")),
            )
            .distinct()
            .all()
        )
    return {row[0] for row in rows if row[0]}


def load_results() -> dict[str, dict[int, dict]]:
    from database.connection import SessionLocal
    from database.models import RuntimeEvent

    grouped: dict[str, dict[int, dict]] = defaultdict(dict)
    with SessionLocal() as db:
        events = (
            db.query(RuntimeEvent)
            .filter(
                RuntimeEvent.account_id.in_(ACCOUNTS),
                RuntimeEvent.event_type.in_(("run.result", "run.failed")),
            )
            .all()
        )
        for event in events:
            payload = event.payload
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                continue
            round_id = payload.get("decision_round_id") or event.decision_round_id
            if not round_id:
                continue
            grouped[str(round_id)][int(event.account_id)] = {
                "account_id": int(event.account_id),
                "event_type": event.event_type,
                "termination_reason": payload.get("termination_reason"),
                "error_type": payload.get("error_type"),
                "code": payload.get("code"),
                "executed_trades": payload.get("executed_trades") or [],
                "summary": (payload.get("summary") or "")[:240],
                "metadata": payload.get("metadata") or {},
            }
    return grouped


def main() -> None:
    from wave3_runtime import ROOT, configure

    configure()
    started = datetime.now(timezone.utc)
    baseline = existing_rounds()
    evidence = ROOT / ".wave3/evidence/wave3-three-complete-cycles.json"
    cycles = []
    seen = set(baseline)
    deadline = time.time() + LIMIT_SECONDS
    print(json.dumps({
        "watching_from": started.isoformat(),
        "baseline_rounds": len(baseline),
    }, ensure_ascii=False), flush=True)
    while time.time() < deadline:
        grouped = load_results()
        for round_id, accounts in grouped.items():
            if round_id in seen:
                continue
            if set(accounts) != set(ACCOUNTS):
                continue
            seen.add(round_id)
            cycle = {
                "index": len(cycles) + 1,
                "decision_round_id": round_id,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "accounts": accounts,
            }
            if not is_complete_cycle(accounts):
                cycles = []
                evidence.write_text(json.dumps({
                    "started_at": started.isoformat(),
                    "cycles": cycles,
                    "last_rejected": cycle,
                }, ensure_ascii=False, indent=2, default=str))
                print(json.dumps({
                    "cycle": cycle["index"],
                    "round": round_id,
                    "accepted": False,
                    "reasons": {
                        aid: item.get("termination_reason")
                        for aid, item in accounts.items()
                    },
                    "event_types": {
                        aid: item.get("event_type")
                        for aid, item in accounts.items()
                    },
                }, ensure_ascii=False), flush=True)
                continue
            cycles.append(cycle)
            print(json.dumps({"cycle": cycle["index"], "round": round_id, "reasons": {
                aid: item["termination_reason"] for aid, item in accounts.items()
            }}, ensure_ascii=False), flush=True)
            evidence.write_text(json.dumps({
                "started_at": started.isoformat(),
                "cycles": cycles,
            }, ensure_ascii=False, indent=2, default=str))
            if len(cycles) >= NEEDED:
                print(json.dumps({"passed": True, "cycles": len(cycles)}), flush=True)
                return
        time.sleep(POLL_SECONDS)
    raise SystemExit(f"Timed out after {LIMIT_SECONDS}s with {len(cycles)} complete cycles")


if __name__ == "__main__":
    main()
