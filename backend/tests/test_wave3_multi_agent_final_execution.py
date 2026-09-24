"""Multi-Agent final open executes through the production Invoker and Gateway."""

import json
from decimal import Decimal

import pytest

from benchmark.agents import AgentRuntime, AgentSelection
from benchmark.application import trading
from benchmark.builtin.agents._legacy_ports import InvokerBackedToolRegistry
from benchmark.builtin.agents.multi_agent import MULTI_AGENT_COMPONENT_ID
from benchmark.contracts import KNOWN_CAPABILITIES, TerminationReason
from benchmark.extensions.host import get_extension_runtime
from benchmark.tools import SynchronousToolInvoker
from database.models import Account, Order, Position, Trade, TradeCommandReceipt
from services.agent.multi_agent import MULTI_AGENT_FINAL_TOOL_CALL_ID
from tests.agents.builtin.conftest import make_decision_context
from tests.agents.builtin.test_multi_agent_migration import (
    _build_context,
    _close_finish_response,
    _frozen_registry,
    _open_finish_response,
)
from tests.fakes import FakeLLM, FakeLLMResponse
from trading import test_gateway_reliability as reliability

session_factory = reliability.session_factory


def test_multi_agent_final_open_replays_are_gateway_idempotent(session_factory, monkeypatch):
    from services import asset_calculator, market_data, order_executor_leverage
    from services.agent import trade_execution_tool as trade_tool_module

    def quote(*args, **kwargs):
        return 100.0

    monkeypatch.setattr(market_data, "get_last_price", quote)
    monkeypatch.setattr(market_data, "get_trading_price", quote)
    monkeypatch.setattr(asset_calculator, "get_last_price", quote)
    monkeypatch.setattr(trade_tool_module, "get_last_price", quote)
    monkeypatch.setattr(order_executor_leverage, "get_last_price", quote)

    gateway = reliability._gateway(session_factory)
    monkeypatch.setattr(trading, "get_default_trade_gateway", lambda: gateway)

    def run_once(round_id):
        invoker = SynchronousToolInvoker(
            get_extension_runtime().tools,
            account_id=1,
            decision_round_id=round_id,
            trace_id=f"trace-{round_id}",
            capabilities=frozenset(KNOWN_CAPABILITIES),
            enabled_tools=("core.execute_trade",),
        )
        return AgentRuntime(
            _frozen_registry(),
            _build_context(
                FakeLLM([FakeLLMResponse(_open_finish_response(account_id=999))]),
                InvokerBackedToolRegistry(invoker),
            ),
        ).run(
            AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
            make_decision_context(
                account_id=1,
                trace_id=f"trace-{round_id}",
                decision_round_id=round_id,
            ),
        )

    first = run_once("round-multi-final")
    with session_factory() as db:
        cash_after_first = db.query(Account).filter(Account.id == 1).one().current_cash
        quantity_after_first = (
            db.query(Position)
            .filter(Position.account_id == 1, Position.symbol == "SOL")
            .one()
            .quantity
        )
        orders_after_first = db.query(Order).count()
    second = run_once("round-multi-final")
    with session_factory() as db:
        assert db.query(Account).filter(Account.id == 1).one().current_cash == cash_after_first
        assert (
            db.query(Position)
            .filter(Position.account_id == 1, Position.symbol == "SOL")
            .one()
            .quantity
            == quantity_after_first
        )
        assert db.query(Order).count() == orders_after_first
    third = run_once("round-multi-final-b")

    assert first.termination_reason is TerminationReason.TRADE_DONE
    assert first.executed_trades[0].executed is True
    assert first.executed_trades[0].symbol == "SOL"
    assert first.executed_trades[0].order_id == second.executed_trades[0].order_id
    assert third.executed_trades[0].order_id != first.executed_trades[0].order_id

    with session_factory() as db:
        account = db.query(Account).filter(Account.id == 1).one()
        receipts = db.query(TradeCommandReceipt).order_by(TradeCommandReceipt.id).all()
        orders = db.query(Order).order_by(Order.id).all()
        trades = db.query(Trade).order_by(Trade.id).all()
        position = (
            db.query(Position)
            .filter(Position.account_id == 1, Position.symbol == "SOL")
            .one()
        )
        assert [item.idempotency_key for item in receipts] == [
            f"round-multi-final:{MULTI_AGENT_FINAL_TOOL_CALL_ID}",
            f"round-multi-final-b:{MULTI_AGENT_FINAL_TOOL_CALL_ID}",
        ]
        assert all(item.account_id == 1 for item in receipts)
        assert '"account_id": 999' not in (receipts[0].command_json or "")
        assert len(orders) == len(trades) == 2
        assert first.executed_trades[0].order_id == orders[0].id
        assert third.executed_trades[0].order_id == orders[1].id
        if first.executed_trades[0].trade_id is not None:
            assert first.executed_trades[0].trade_id == trades[0].id
        assert cash_after_first < Decimal("10000")
        assert account.current_cash < cash_after_first
        assert position.quantity > quantity_after_first


def test_multi_agent_final_close_zero_portion_closes_through_gateway(
    session_factory, monkeypatch
):
    from services import asset_calculator, market_data, order_executor_leverage
    from services.agent import trade_execution_tool as trade_tool_module

    def quote(*args, **kwargs):
        return 100.0

    monkeypatch.setattr(market_data, "get_last_price", quote)
    monkeypatch.setattr(market_data, "get_trading_price", quote)
    monkeypatch.setattr(asset_calculator, "get_last_price", quote)
    monkeypatch.setattr(trade_tool_module, "get_last_price", quote)
    monkeypatch.setattr(order_executor_leverage, "get_last_price", quote)

    gateway = reliability._gateway(session_factory)
    monkeypatch.setattr(trading, "get_default_trade_gateway", lambda: gateway)

    def run_decision(round_id, response):
        invoker = SynchronousToolInvoker(
            get_extension_runtime().tools,
            account_id=1,
            decision_round_id=round_id,
            trace_id=f"trace-{round_id}",
            capabilities=frozenset(KNOWN_CAPABILITIES),
            enabled_tools=("core.execute_trade",),
        )
        return AgentRuntime(
            _frozen_registry(),
            _build_context(
                FakeLLM([FakeLLMResponse(response)]),
                InvokerBackedToolRegistry(invoker),
            ),
        ).run(
            AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
            make_decision_context(
                account_id=1,
                trace_id=f"trace-{round_id}",
                decision_round_id=round_id,
            ),
        )

    opened = run_decision("round-multi-close-open", _open_finish_response())
    with session_factory() as db:
        cash_after_open = db.query(Account).filter(Account.id == 1).one().current_cash
        position_after_open = (
            db.query(Position)
            .filter(Position.account_id == 1, Position.symbol == "SOL")
            .one()
        )
        open_qty = position_after_open.quantity
    closed = run_decision("round-multi-close-zero", _close_finish_response())

    assert opened.termination_reason is TerminationReason.TRADE_DONE
    assert opened.executed_trades[0].executed is True
    assert closed.termination_reason is TerminationReason.TRADE_DONE
    assert closed.executed_trades[0].executed is True
    assert closed.executed_trades[0].symbol == "SOL"
    assert closed.metadata["legacy_operation"] == "close"

    with session_factory() as db:
        account = db.query(Account).filter(Account.id == 1).one()
        position = (
            db.query(Position)
            .filter(Position.account_id == 1, Position.symbol == "SOL")
            .one()
        )
        receipts = db.query(TradeCommandReceipt).order_by(TradeCommandReceipt.id).all()
        orders = db.query(Order).order_by(Order.id).all()
        trades = db.query(Trade).order_by(Trade.id).all()
        assert open_qty > 0
        assert cash_after_open < Decimal("10000")
        assert position.quantity == 0
        assert account.current_cash > cash_after_open
        assert [item.idempotency_key for item in receipts] == [
            f"round-multi-close-open:{MULTI_AGENT_FINAL_TOOL_CALL_ID}",
            f"round-multi-close-zero:{MULTI_AGENT_FINAL_TOOL_CALL_ID}",
        ]
        assert len(orders) == len(trades) == 2
        assert closed.executed_trades[0].order_id == orders[1].id
        command = json.loads(receipts[1].command_json)
        assert command["operation"] == "close"
        assert command["sizing_mode"] == "close_ratio"
        assert command["sizing_value"] == "1"


def test_execute_trade_close_usd_without_amount_is_rejected_by_gateway(
    session_factory, monkeypatch
):
    from benchmark.contracts.errors import TradeGatewayError
    from services.agent.trade_execution_tool import execute_trade_tool

    gateway = reliability._gateway(session_factory)
    with pytest.raises(TradeGatewayError) as caught:
        execute_trade_tool(
            db=object(),
            account_id=1,
            operation="close",
            symbol="SOL",
            market="CRYPTO",
            direction="long",
            size_mode="usd",
            decision_round_id="round-close-usd-missing",
            tool_call_id="call-1",
            gateway=gateway,
        )
    assert caught.value.code == "SIZING_VALUE_INVALID"


def test_multi_agent_invoker_schema_invalid_leverage_is_tool_error():
    invoker = SynchronousToolInvoker(
        get_extension_runtime().tools,
        account_id=1,
        decision_round_id="round-multi-leverage-schema",
        trace_id="trace-multi-leverage-schema",
        capabilities=frozenset(KNOWN_CAPABILITIES),
        enabled_tools=("core.execute_trade",),
    )
    result = AgentRuntime(
        _frozen_registry(),
        _build_context(
            FakeLLM([FakeLLMResponse(_open_finish_response(leverage=11))]),
            InvokerBackedToolRegistry(invoker),
        ),
    ).run(
        AgentSelection(MULTI_AGENT_COMPONENT_ID, config={"max_steps": 2}),
        make_decision_context(
            account_id=1,
            trace_id="trace-multi-leverage-schema",
            decision_round_id="round-multi-leverage-schema",
        ),
    )
    assert result.termination_reason is TerminationReason.TOOL_ERROR
    assert result.executed_trades[0].executed is False
    assert result.executed_trades[0].reject_code == "TOOL_INPUT_INVALID"
