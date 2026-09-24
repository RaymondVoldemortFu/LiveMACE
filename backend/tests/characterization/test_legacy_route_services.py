from benchmark.persistence.compliance import compliance_service
from datetime import datetime, timedelta

from database.models import (
    AIDecisionLog,
    Account,
    AgentMemory,
    AgentPeriodCheckpoint,
    AgentTrace,
    MarketKline,
    RuleEvaluationResult,
    User,
)
from services.account_api_service import AccountApiService
from services.agent_api_service import AgentApiService
from benchmark.persistence.compliance import compliance_service as ComplianceApiService
from services.evaluation_api_service import EvaluationApiService
from services.memory_api_service import MemoryApiService
from services.ranking_api_service import RankingApiService


def _account(db_session, name="agent"):
    user = User(username=f"{name}-user")
    db_session.add(user)
    db_session.flush()
    account = Account(
        user_id=user.id,
        name=name,
        initial_capital=10000,
        current_cash=10000,
        frozen_cash=0,
    )
    db_session.add(account)
    db_session.flush()
    return account, user


def test_account_service_preserves_account_lookup_contract(db_session):
    account, user = _account(db_session)
    service = AccountApiService(db_session)

    assert service.list_active_accounts() == [account]
    assert service.get_active_account(account.id) is account
    assert service.get_default_active_account() is account
    assert service.get_user(user.id) is user
    assert service.get_position_order_counts(account.id) == (0, 0)


def test_agent_service_parses_trace_and_preserves_history_fallback(db_session):
    account, _ = _account(db_session)
    now = datetime.utcnow()
    db_session.add(
        AgentTrace(
            trace_id="trace-1",
            account_id=account.id,
            step_number=1,
            role="tool",
            content="done",
            tool_calls='[{"name": "quote"}]',
            tool_output="{'price': 10}",
            created_at=now,
        )
    )
    db_session.flush()

    service = AgentApiService(db_session)
    trace = service.get_trace("trace-1")
    assert trace["steps"][0]["tool_calls"] == [{"name": "quote"}]
    assert trace["steps"][0]["tool_output"] == {"price": 10}
    assert service.get_latest_trace(account.id) == {"trace_id": "trace-1"}
    assert service.get_trace_history(account.id, 20)[0]["trace_id"] == "trace-1"


def test_compliance_service_preserves_history_stats_and_decision_join(db_session):
    account, _ = _account(db_session)
    now = datetime.utcnow()
    db_session.add_all(
        [
            RuleEvaluationResult(
                account_id=account.id,
                trace_id="trace-1",
                ts=now,
                gate_pass="true",
                s_rule_sat=0.8,
                s_audit=0.7,
                final_score=0.75,
                llm_audit_score=4.0,
            ),
            AIDecisionLog(
                account_id=account.id,
                decision_time=now,
                reason="reason",
                operation="hold",
                target_portion=0,
                total_balance=10000,
                trace_id="trace-1",
            ),
        ]
    )
    db_session.flush()

    service = ComplianceApiService(db_session)
    assert service.history(account.id, 50, 0)["records"][0]["gate_pass"] is True
    assert service.stats(account.id)["all_time"]["avg_final_score"] == 0.75
    assert service.trend(account.id, "day", "final_score")["data_points"][0]["value"] == 0.75
    assert service.recent_decisions(account.id, 20)["decisions"][0]["compliance"]["final_score"] == 0.75


def test_evaluation_service_preserves_checkpoint_views(db_session):
    account, _ = _account(db_session)
    end = datetime.utcnow().replace(microsecond=0)
    db_session.add(
        AgentPeriodCheckpoint(
            account_id=account.id,
            interval_seconds=3600,
            period_start=end - timedelta(hours=1),
            period_end=end,
            equity_start=10000,
            equity_end=10100,
            pnl=100,
            return_rate=0.01,
            volatility=0.02,
        )
    )
    db_session.flush()

    service = EvaluationApiService(db_session)
    assert service.list_account_checkpoints(account.id, 3600, 10)["items"][0]["pnl"] == 100.0
    assert service.leaderboard(3600, None, 10, "return")["items"][0]["agent_name"] == "agent"
    assert service.compare_agents(3600, None, None, 10)["items"][0]["return_rate"] == 0.01


def test_memory_service_preserves_list_and_growth_views(db_session):
    account, _ = _account(db_session)
    now = datetime.utcnow()
    db_session.add_all(
        [
            AgentMemory(memory_id="m1", account_id=account.id, market="CRYPTO", content="one", created_at=now),
            AgentMemory(
                memory_id="m2",
                account_id=account.id,
                market="CRYPTO",
                content="two",
                created_at=now + timedelta(days=1),
            ),
        ]
    )
    db_session.flush()

    service = MemoryApiService(db_session)
    assert [item["content"] for item in service.list_memories(account.id, "CRYPTO")["memories"]] == [
        "two",
        "one",
    ]
    assert service.get_growth_timeline(account.id, None)["timeline"][-1]["cumulative_count"] == 2


def test_ranking_service_preserves_symbol_and_empty_factor_views(db_session):
    today = datetime.now().date().isoformat()
    db_session.add(
        MarketKline(symbol="BTC", period="1d", timestamp=1, datetime_str=today)
    )
    db_session.flush()

    service = RankingApiService(db_session)
    assert service.available_symbols(100)["symbols"] == ["BTC"]
    assert service.ranking_table(100, None, 50)["message"] == "Insufficient data for factor calculation"
