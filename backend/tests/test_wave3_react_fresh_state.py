"""ReAct observes committed state and delegates write idempotency to Gateway."""
import json
from decimal import Decimal

from benchmark.builtin.tools.account import _default_account_reader
from benchmark.contracts import TerminationReason
from database.models import Order, Trade
from tests.agents.builtin.test_react_migration import _full_tool_registry, _register, _run_react
from tests.fakes import FakeLLM, FakeLLMResponse, FakeToolCall
from trading import test_gateway_reliability as reliability

session_factory = reliability.session_factory


def test_account_reads_after_identical_trades_are_fresh_and_not_duplicate_blocked(session_factory, monkeypatch):
    from database import connection
    from services import order_executor_leverage
    from services.agent import trade_execution_tool
    monkeypatch.setattr(connection, 'SessionLocal', session_factory)
    monkeypatch.setattr(trade_execution_tool, 'get_last_price', lambda *a:100.0)
    monkeypatch.setattr(trade_execution_tool, 'calc_positions_value', lambda *a:0.0)
    monkeypatch.setattr(order_executor_leverage, 'get_last_price', lambda *a:100.0)
    registry = _full_tool_registry()
    observed=[]
    def read(**kwargs):
        state=_default_account_reader(1)
        observed.append(state)
        return state
    def trade(**kwargs):
        result=reliability._gateway(session_factory).execute(reliability._command(
            key=kwargs['tool_call_id'],direction='long',sizing_mode='usd',
            sizing_value=Decimal('100'),leverage=1,
        ))
        return {'executed':result.executed,'operation':'open','symbol':'BTC','market':'CRYPTO','order_id':result.order_id}
    _register(registry,'get_account_state',read)
    _register(registry,'execute_trade',trade)
    def response(i,name,args=None):
        return FakeLLMResponse(None,[FakeToolCall(i,name,json.dumps(args or {}))])
    args={'operation':'open','symbol':'BTC','market':'CRYPTO','size_mode':'usd','usd_amount':100}
    responses=[response('read0','get_account_state'), response('trade1','execute_trade',args),response('read1','get_account_state'),response('trade2','execute_trade',args)]
    responses += [response(f'read{i}','get_account_state') for i in range(2,8)]
    responses.append(FakeLLMResponse('Verified updated positions. <TRADE_DONE>'))
    result=_run_react(FakeLLM(responses),registry,{'max_steps':12,'tool_routing_enabled':False,'memory_enabled':False})
    assert result.termination_reason is TerminationReason.TRADE_DONE
    assert len(observed)==8
    assert observed[0]['positions']==[]
    assert observed[1]['positions'][0]['quantity']==1
    assert observed[-1]['positions'][0]['quantity']==2
    assert observed[0]['account']['cash'] > observed[1]['account']['cash'] > observed[-1]['account']['cash']
    with session_factory() as db:
        assert db.query(Order).count()==db.query(Trade).count()==2
