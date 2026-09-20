"""Round summaries describe completed runs without impersonating trade events."""

from decimal import Decimal

import pytest

from benchmark.application.decisions import runner
from benchmark.contracts import AgentRunResult, ExecutedTradeRef, Market, TerminationReason
from database.models import Account, AccountSnapshot, AIDecisionLog, Order, RuleEvaluationResult, Trade
from trading import test_gateway_reliability as reliability

session_factory = reliability.session_factory


@pytest.mark.parametrize("direction", ["long", "short"])
def test_summary_uses_post_trade_equity_and_preserves_audit_without_reexecuting(
    session_factory, monkeypatch, direction,
):
    from services import market_data, order_executor_leverage
    from services.agent import trade_execution_tool

    monkeypatch.setattr(runner, "SessionLocal", session_factory)
    monkeypatch.setattr(trade_execution_tool, "get_last_price", lambda *args: 100.0)
    monkeypatch.setattr(trade_execution_tool, "calc_positions_value", lambda *args: 0.0)
    monkeypatch.setattr(order_executor_leverage, "get_last_price", lambda *args: 100.0)
    with session_factory() as db:
        db.get(Account, 1).enable_rule_aware = "true"
        db.commit()
    trade = reliability._gateway(session_factory).execute(reliability._command(
        key="summary-open", direction=direction, sizing_mode="usd",
        sizing_value=Decimal("100"), leverage=2,
    ))
    assert trade.executed
    result = AgentRunResult(
        "summary-trace", "summary-round", TerminationReason.TRADE_DONE,
        executed_trades=(ExecutedTradeRef("open", "BTC", Market.CRYPTO, trade.order_id, trade.trade_id, True),),
        summary="Opened the position. credential-test",
        metadata={
            "compliance_audit": {"final_status": "PASS", "s_rule_sat": 0.8},
            "llm_audit": {"final_normalized_score": 0.6},
            "agent_reasoning": "Evidence supports the trade.",
        },
    )

    def no_network(*args, **kwargs):
        raise AssertionError("Summary persistence must use the supplied valuation quotes")

    monkeypatch.setattr(market_data, "get_trading_price", no_network)
    monkeypatch.setattr(market_data, "get_last_price", no_network)
    runner._save_run_summary(1, result, {"BTC": 100}, secrets=("credential-test",))
    with session_factory() as db:
        assert db.query(Order).count() == db.query(Trade).count() == 1
        logs = db.query(AIDecisionLog).order_by(AIDecisionLog.id).all()
        assert [row.operation for row in logs] == ["open", "summary"]
        assert [row.executed for row in logs] == ["true", "false"]
        summary = logs[-1]
        assert summary.trace_id == "summary-trace"
        assert summary.order_id is summary.execution_price is summary.execution_quantity is None
        assert summary.total_balance == Decimal("9999.93")
        assert "Opened the position." in summary.reason
        assert "credential-test" not in summary.reason
        snapshot = db.query(AccountSnapshot).order_by(AccountSnapshot.id.desc()).first()
        assert snapshot is not None
        assert snapshot.total_equity == summary.total_balance
        assert snapshot.cash == db.get(Account, 1).current_cash
        audit = db.query(RuleEvaluationResult).filter_by(trace_id="summary-trace").one()
        assert audit.gate_pass == "true"
        assert float(audit.final_score) == pytest.approx(0.7)


@pytest.mark.parametrize("termination", [TerminationReason.HOLD, TerminationReason.MAX_STEPS])
def test_empty_round_summary_has_explicit_status_without_an_executed_hold(
    session_factory, monkeypatch, termination,
):
    monkeypatch.setattr(runner, "SessionLocal", session_factory)
    result = AgentRunResult("trace-empty", "round-empty", termination)
    runner._save_run_summary(1, result, {})
    with session_factory() as db:
        log = db.query(AIDecisionLog).one()
        assert log.operation == "summary" and log.executed == "false"
        assert log.reason == f"Round finished: {termination.value}."
        assert log.total_balance == Decimal("10000")
        assert db.query(Order).count() == db.query(Trade).count() == 0
        assert db.query(AccountSnapshot).count() == 1


@pytest.mark.parametrize("trade", [False, True])
def test_react_adapter_retains_final_assistant_text_after_tool_mode_completion(trade):
    import json
    from tests.agents.builtin.test_react_migration import _full_tool_registry, _run_react
    from tests.fakes import FakeLLM, FakeLLMResponse, FakeToolCall

    responses = []
    if trade:
        responses.append(FakeLLMResponse(None, [FakeToolCall(
            "call-1", "execute_trade", json.dumps({"operation": "open", "symbol": "BTC", "market": "CRYPTO"}),
        )]))
    responses.append(FakeLLMResponse("Kept exposure within the risk budget. <TRADE_DONE>"))
    result = _run_react(
        FakeLLM(responses), _full_tool_registry(),
        {"max_steps": 3, "tool_routing_enabled": False, "memory_enabled": False},
    )
    assert result.summary == "Kept exposure within the risk budget."
    assert result.termination_reason == (TerminationReason.TRADE_DONE if trade else TerminationReason.HOLD)
    assert len(result.executed_trades) == (1 if trade else 0)


def test_react_token_only_does_not_relabel_prior_trade_plan_as_final_summary():
    from benchmark.builtin.agents.react import _summary_from_legacy_result

    steps = [
        {"role": "assistant", "content": "I plan to buy BTC.", "tool_calls": [{"id": "trade"}]},
        {"role": "tool", "content": "filled"},
        {"role": "assistant", "content": "<TRADE_DONE>"},
    ]
    assert _summary_from_legacy_result({"reason": ""}, steps) == ""
