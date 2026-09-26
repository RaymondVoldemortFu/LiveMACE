"""Post-tool compliance uses committed balances and audits every executed order."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Account, Base, Position, User
from services.agent.rule_aware.rule_aware_agent import RuleAwareAgent
from services.agent.rule_aware.rule_engine import RuleEngine
from services.agent.rule_aware.rule_validator import RuleValidator
from services.agent.rule_aware.compliance_auditor import ComplianceAuditor


@pytest.fixture
def audit_system(monkeypatch):
    from database import connection

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(connection, "SessionLocal", sessions)
    with sessions() as db:
        db.add(User(id=1, username="isolated-rule-audit"))
        db.add(
            Account(
                id=1,
                user_id=1,
                name="audit",
                current_cash=7200,
                frozen_cash=0,
                margin_used=0,
            )
        )
        db.add_all(
            [
                Position(
                    account_id=1,
                    symbol="SOL",
                    name="SOL",
                    market="CRYPTO",
                    quantity=14,
                    avg_cost=100,
                    leverage=1,
                    side="LONG",
                ),
                Position(
                    account_id=1,
                    symbol="ETH",
                    name="ETH",
                    market="CRYPTO",
                    quantity=14,
                    avg_cost=100,
                    leverage=1,
                    side="LONG",
                ),
            ]
        )
        db.commit()
    rules = RuleEngine(str(Path(__file__).resolve().parents[2] / "config/rules"))
    agent = object.__new__(RuleAwareAgent)
    agent.rule_engine = rules
    agent.rule_validator = RuleValidator(rules)
    agent.compliance_auditor = ComplianceAuditor(rules, agent.rule_validator)
    agent.enable_llm_audit = False
    agent.llm_auditor = None
    yield agent, sessions
    engine.dispose()


def trade(symbol, amount, *, executed=True):
    return {
        "args": {
            "operation": "open",
            "symbol": symbol,
            "market": "CRYPTO",
            "direction": "long",
            "leverage": 1,
            "size_mode": "usd",
            "usd_amount": amount,
        },
        "result": {
            "executed": executed,
            "operation": "open",
            "symbol": symbol,
            "notional_usd": amount,
            "order_id": {"SOL": 1, "ETH": 2}[symbol],
        },
    }


def run_audit(agent, trades, prices=None):
    original = {"account_id": 1, "cash": 10000, "total_assets": 10000, "positions": {}}
    result = agent._build_tool_mode_decision(
        trades, "<TRADE_DONE>", original, prices or {"SOL": 100, "ETH": 100}
    )
    assert original["cash"] == 10000
    assert original["positions"] == {}
    return result["compliance_audit"]


def test_real_round_shape_uses_post_trade_cash_and_preserves_sector_definition(
    audit_system,
):
    agent, _ = audit_system
    audit = run_audit(agent, [trade("SOL", 1400), trade("ETH", 1400)])
    violations = {v["rule_id"]: v for v in audit["violations"]}
    assert violations["R2-02"]["actual_value"] == pytest.approx(0.72)
    # Both invested holdings are SmartContract; cash is outside this rule's denominator.
    assert violations["R2-03"]["actual_value"] == 1.0
    assert (
        "R1-02" not in violations
    )  # 14% filled positions are not added a second time.
    assert violations["R2-04"]["actual_value"] == pytest.approx(0.14)
    assert audit["portfolio_basis"]["cash"] == 7200
    assert audit["portfolio_basis"]["total_equity"] == 10000
    assert audit["portfolio_basis"]["audit_phase"] == "post_execution"
    assert (
        audit["sector_allocation_basis"]
        == "preferred_notional / total_position_notional"
    )


def test_first_oversized_order_is_checked_even_when_last_order_is_small(audit_system):
    agent, sessions = audit_system
    with sessions() as db:
        db.get(Account, 1).current_cash = 6500
        for position in db.query(Position).all():
            position.quantity = 25 if position.symbol == "SOL" else 10
        db.commit()
    audit = run_audit(agent, [trade("SOL", 2500), trade("ETH", 1000)])
    violations = {v["rule_id"]: v for v in audit["violations"]}
    assert audit["final_status"] == "FAIL"
    assert violations["R0-04"]["actual_value"] == 0.25
    assert violations["R1-02"]["actual_value"] == 0.25
    assert "SOL" in violations["R1-02"]["message"]


def test_rejected_order_is_not_scored_as_a_fill(audit_system):
    agent, _ = audit_system
    audit = run_audit(agent, [trade("SOL", 9900, executed=False), trade("ETH", 1400)])
    assert not any(v["rule_id"] == "R0-04" for v in audit["violations"])


def test_each_soft_rule_uses_worst_executed_order_score(audit_system):
    agent, _ = audit_system
    audit = run_audit(agent, [trade("SOL", 500), trade("ETH", 100)])
    assert audit["r2_results"]["rule_scores"]["R2-05"] == pytest.approx(0.19)


def test_sector_check_uses_same_snapshot_when_ledger_changes_later(audit_system):
    agent, sessions = audit_system
    snapshot = agent.compliance_auditor.post_execution_portfolio(
        {"account_id": 1}, {"SOL": 100, "ETH": 100}
    )
    with sessions() as db:
        db.query(Position).delete()
        db.commit()
    _, violations = agent.rule_validator.validate_decision(
        {"operation": "hold"}, snapshot, {}
    )
    sector = next(v for v in violations if v.rule.id == "R2-03")
    assert sector.actual_value == 1.0


def test_portfolio_leverage_does_not_multiply_quantity_by_leverage_twice(audit_system):
    agent, sessions = audit_system
    with sessions() as db:
        db.query(Position).delete()
        db.get(Account, 1).current_cash = 0
        db.get(Account, 1).margin_used = 12000
        db.add(
            Position(
                account_id=1,
                symbol="SOL",
                name="SOL",
                market="CRYPTO",
                quantity=600,
                avg_cost=100,
                leverage=5,
                side="LONG",
            )
        )
        db.commit()
    snapshot = agent.compliance_auditor.post_execution_portfolio(
        {"account_id": 1}, {"SOL": 100}
    )
    assert snapshot["total_equity"] == 12000
    _, violations = agent.rule_validator.validate_decision(
        {"operation": "open", "symbol": "SOL", "leverage": 5}, snapshot, {"SOL": 100}
    )
    assert not any(v.rule.id == "R0-01" for v in violations)


def test_llm_audit_receives_the_same_post_execution_snapshot(audit_system, monkeypatch):
    agent, _ = audit_system
    monkeypatch.setenv("AUDIT_OFFLINE", "false")
    captured = []

    def audit_agent_reasoning(**kwargs):
        captured.append(kwargs["market_state"]["portfolio"])
        return {"final_normalized_score": 1.0}

    agent.enable_llm_audit = True
    agent.llm_auditor = SimpleNamespace(
        audit_agent_reasoning=audit_agent_reasoning,
        format_audit_report=lambda result: "test",
    )
    run_audit(agent, [trade("SOL", 1400), trade("ETH", 1400)])
    assert captured[0]["cash"] == 7200
    assert captured[0]["total_equity"] == 10000


def test_audit_snapshot_failure_is_explicit_and_does_not_fabricate_old_score(
    audit_system, monkeypatch
):
    agent, _ = audit_system

    def unavailable(*args):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(
        agent.compliance_auditor, "post_execution_portfolio", unavailable
    )
    audit = run_audit(agent, [trade("SOL", 1400)])
    assert audit["final_status"] == "ERROR"
    assert audit["s_rule_sat"] is None
    assert "r2_results" not in audit


def test_all_in_is_audited_using_the_executed_notional(audit_system):
    agent, _ = audit_system
    filled = trade("SOL", 9900)
    filled["args"] = {"operation": "all_in", "symbol": "SOL", "market": "CRYPTO"}
    audit = run_audit(agent, [filled])
    violation = next(v for v in audit["violations"] if v["rule_id"] == "R0-04")
    assert violation["actual_value"] == pytest.approx(0.99)
    assert audit["final_status"] == "FAIL"


def test_close_all_reads_each_committed_fill_and_does_not_change_ledger(audit_system):
    from database.models import Order, Trade

    agent, sessions = audit_system
    with sessions() as db:
        for order_id, symbol, quantity in [(1, "SOL", 50), (2, "ETH", 10)]:
            db.add(Order(id=order_id, account_id=1, order_no=f"close-{order_id}",
                         symbol=symbol, name=symbol, market="CRYPTO", side="SELL",
                         order_type="MARKET", quantity=quantity, leverage=1, status="FILLED"))
            for _ in range(2):
                db.add(Trade(order_id=order_id, account_id=1, symbol=symbol, name=symbol,
                             market="CRYPTO", side="SELL", price=100, quantity=quantity / 2))
        db.commit()
    batch = {"args": {"operation": "close_all"}, "result": {
        "executed": True, "operation": "close_all", "closed_orders": [
            {"order_id": 1, "symbol": "SOL", "market": "CRYPTO", "quantity": 50},
            {"order_id": 2, "symbol": "ETH", "market": "CRYPTO", "quantity": 10},
        ],
    }}
    audit = run_audit(agent, [batch])
    assert audit["final_status"] == "FAIL"
    violation = next(v for v in audit["violations"] if v["rule_id"] == "R0-04")
    assert violation["actual_value"] == pytest.approx(0.5)
    with sessions() as db:
        assert db.query(Trade).count() == 4
        assert db.query(Order).count() == 2
        assert db.get(Account, 1).current_cash == 7200


def test_close_fill_without_notional_marks_audit_unavailable(audit_system):
    agent, _ = audit_system
    audit = run_audit(agent, [{"args": {"operation": "close_all"}, "result": {
        "executed": True, "operation": "close_all",
        "closed_orders": [{"order_id": 999, "symbol": "SOL", "quantity": 10}],
    }}])
    assert audit["final_status"] == "ERROR"
    assert audit["s_rule_sat"] is None
    assert "r2_results" not in audit


@pytest.mark.parametrize("failed_rule", ["R0-01", "R1-01", "R2-01"])
def test_rule_failure_does_not_produce_a_numeric_score(audit_system, monkeypatch, failed_rule):
    agent, _ = audit_system
    original = agent.rule_validator._check_rule

    def check(rule, *args, **kwargs):
        if rule.id == failed_rule:
            raise RuntimeError("rule unavailable")
        return original(rule, *args, **kwargs)

    monkeypatch.setattr(agent.rule_validator, "_check_rule", check)
    audit = run_audit(agent, [trade("SOL", 1400)])
    assert audit["final_status"] == "ERROR"
    assert audit["s_rule_sat"] is None
    assert "r2_results" not in audit


@pytest.mark.parametrize("failed_model", ["AccountSnapshot", "AssetMetadata", "AIDecisionLog"])
def test_history_query_failure_is_not_silently_scored(audit_system, monkeypatch, failed_model):
    agent, sessions = audit_system
    original_query = sessions.class_.query

    def query(db, *entities, **kwargs):
        if any(getattr(entity, "__name__", None) == failed_model for entity in entities):
            raise RuntimeError("history unavailable")
        return original_query(db, *entities, **kwargs)

    monkeypatch.setattr(sessions.class_, "query", query)
    audit = run_audit(agent, [trade("SOL", 1400)])
    assert audit["final_status"] == "ERROR"
    assert audit["s_rule_sat"] is None


def test_duplicate_tool_result_does_not_audit_same_order_twice(audit_system, monkeypatch):
    agent, _ = audit_system
    checked = []
    original = agent.rule_validator.validate_decision

    def validate(action, *args):
        checked.append(action)
        return original(action, *args)

    monkeypatch.setattr(agent.rule_validator, "validate_decision", validate)
    filled = trade("SOL", 1400)
    run_audit(agent, [filled, filled])
    assert len(checked) == 1


def add_round(db, number, *, termination="hold", trades=(), round_id=None):
    import json
    from datetime import datetime, timedelta
    from database.models import AIDecisionLog, RuntimeEvent

    round_id = round_id or f"round-{number}"
    timestamp = datetime(2026, 9, 18) + timedelta(minutes=number)
    db.add(RuntimeEvent(id=f"event-{number}", account_id=1, decision_round_id=round_id,
                        trace_id=f"trace-{number}", event_type="run.result", sequence=number,
                        payload=json.dumps({"termination_reason": termination, "executed_trades": trades}),
                        created_at=timestamp))
    db.add(AIDecisionLog(account_id=1, reason="Round completed", operation="summary",
                         trace_id=f"trace-{number}", executed="false", target_portion=0,
                         total_balance=10000, decision_time=timestamp, created_at=timestamp))


def hold_violation(agent, *, round_id=None):
    snapshot = agent.compliance_auditor.post_execution_portfolio(
        {"account_id": 1, "decision_round_id": round_id}, {"SOL": 100, "ETH": 100}
    )
    _, violations = agent.rule_validator.validate_decision({"operation": "hold"}, snapshot, {})
    return next((v for v in violations if v.rule.id == "R2-06"), None)


def test_hold_counts_completed_rounds_once_and_excludes_current_tool_log(audit_system):
    from database.models import AIDecisionLog

    agent, sessions = audit_system
    with sessions() as db:
        add_round(db, 1)
        add_round(db, 2)
        add_round(db, 3, round_id="round-2")
        add_round(db, 4, round_id="current")
        db.add(AIDecisionLog(account_id=1, reason="Current hold tool", operation="hold",
                             executed="true", target_portion=0, total_balance=10000))
        db.commit()
    violation = hold_violation(agent, round_id="current")
    assert violation.actual_value == 3
    assert violation.score == pytest.approx(1 - (2 / 3) ** 2)


@pytest.mark.parametrize("operation", ["open", "close"])
@pytest.mark.parametrize("termination", ["hold", "max_steps"])
def test_actual_fill_breaks_hold_streak_regardless_of_termination(audit_system, operation, termination):
    agent, sessions = audit_system
    with sessions() as db:
        add_round(db, 1)
        add_round(db, 2, termination=termination, trades=[{"operation": operation, "executed": True}])
        add_round(db, 3)
        db.commit()
    assert hold_violation(agent).actual_value == 2


def test_plain_summary_and_current_hold_are_not_prior_hold_rounds(audit_system):
    from database.models import AIDecisionLog

    agent, sessions = audit_system
    with sessions() as db:
        for operation in ("summary", "hold"):
            db.add(AIDecisionLog(account_id=1, reason="Current observation", operation=operation,
                                 executed="false", target_portion=0, total_balance=10000))
        db.commit()
    assert hold_violation(agent) is None


def test_failed_round_is_not_classified_as_hold(audit_system):
    agent, sessions = audit_system
    with sessions() as db:
        add_round(db, 1)
        add_round(db, 2, termination="tool_error", trades=[{"operation": "open", "executed": False}])
        db.commit()
    assert hold_violation(agent) is None


def test_post_execution_reversal_uses_history_before_each_fill(audit_system):
    from datetime import datetime, timedelta
    from database.models import AIDecisionLog

    agent, sessions = audit_system
    now = datetime.utcnow()
    with sessions() as db:
        for number, operation in [(1, "close"), (2, "open"), (3, "open")]:
            db.add(AIDecisionLog(account_id=1, reason="Filled trade", operation=operation,
                                 symbol="SOL", order_id=number, executed="true",
                                 target_portion=0.1, total_balance=10000,
                                 created_at=now - timedelta(minutes=4-number)))
        db.commit()
    filled = trade("SOL", 1400)
    filled["result"]["order_id"] = 2
    audit = run_audit(agent, [filled])
    reversal = next(v for v in audit["violations"] if v["rule_id"] == "R2-01")
    assert reversal["actual_value"] == pytest.approx(1)
    assert reversal["score"] == pytest.approx((1 / 60) ** 2)
