"""API save -> committed config -> next worker -> selected built-in factory."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.extension_routes import router
from benchmark.application.decisions import runner
from benchmark.application.decisions.context_builder import load_worker_input
from benchmark.contracts import AgentRunResult, TerminationReason
from benchmark.extensions import ExtensionSettings, build_extension_runtime
from benchmark.persistence import SqlAlchemyUnitOfWork
from config.api_feature_config import ApiFeatureConfig
from database.connection import Base
from database.models import Account, User
from services.extension_config_service import ExtensionConfigService, get_extension_config_service


def test_four_agents_are_selected_on_next_round_after_api_save(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "round.sqlite"}')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        user = User(username='wave4')
        db.add(user)
        db.flush()
        account = Account(user_id=user.id, name='wave4', account_type='AI', is_active='true', model='fake', api_key='unit-test-credential', base_url='http://invalid.local', initial_capital=10000, current_cash=10000, frozen_cash=0)
        db.add(account)
        db.commit()
        account_id = account.id
    runtime = build_extension_runtime(ExtensionSettings())
    service = ExtensionConfigService(runtime=runtime, uow_factory=lambda: SqlAlchemyUnitOfWork(sessions))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_extension_config_service] = lambda: service
    monkeypatch.setattr(ApiFeatureConfig, 'ENABLE_ACCOUNT_UPDATE_API', True)
    monkeypatch.setattr(runner, 'get_extension_runtime', lambda: runtime)
    monkeypatch.setattr(runner, 'load_worker_input', lambda aid, prices, rid: load_worker_input(aid, prices, rid, uow_factory=lambda: SqlAlchemyUnitOfWork(sessions)))
    monkeypatch.setattr(runner, 'LLMClient', lambda **kw: SimpleNamespace(model='fake', call=lambda **_: (_ for _ in ()).throw(AssertionError('Live LLM forbidden'))))
    monkeypatch.setattr(runner, '_save_run_summary', lambda *a, **kw: None)
    selected = []
    def run(self, selection, context, **kwargs):
        # Instantiate the actual factory against the worker's bound providers.
        agent = self._registry.get(selection.agent_id, selection.version).factory.create(self._build_context, selection.config)
        assert callable(agent.run)
        selected.append((selection.agent_id, context.config, tuple(spec.name for spec in self._build_context.tools.list_specs())))
        return AgentRunResult(trace_id=context.trace_id, decision_round_id=context.decision_round_id, termination_reason=TerminationReason.HOLD)
    monkeypatch.setattr(runner.AgentRuntime, 'run', run)
    events = SimpleNamespace(record=lambda *args: None, emit=lambda *args: None)
    disabled = [spec.name for spec in runtime.tools.list() if 'sandbox.write' in spec.required_capabilities]
    disabled.append('core.search')
    previous = None
    agent_ids = ['core.react', 'core.multi-agent', 'core.advanced-multi-agent', 'core.rule-aware']
    with TestClient(app) as client:
        for index, agent_id in enumerate(agent_ids):
            draft = dict(agent_id=agent_id, agent_config={}, prompt_profile_id=agent_id + '.default', toolset_ids=['core.account-tools', 'core.trading-tools'], disabled_tools=disabled)
            if agent_id == 'core.react':
                draft['agent_config'] = dict(memory_enabled=True, tool_routing_enabled=False)
            valid = client.post(f'/api/account/{account_id}/runtime-config/validate', json={'config':draft})
            assert valid.status_code == 200 and valid.json()['valid'], valid.text
            stored = client.put(f'/api/account/{account_id}/runtime-config', json={'config':valid.json()['config'], 'expected_updated_at':previous})
            assert stored.status_code == 200, stored.text
            previous = stored.json()['updated_at']
            runner._run_account(account_id, {'BTC':60000}, f'round-{index}', events, f'trace-{index}', lambda:False)
            assert selected[-1][0] == agent_id
            assert stored.json()['config']['toolset_ids'] == ['core.account-tools', 'core.trading-tools']
            assert set(selected[-1][2]) == set(runtime.catalog.resolve_tool_names(toolset_ids=('core.account-tools', 'core.trading-tools'), disabled_tools=disabled))
            assert 'core.search' not in selected[-1][2]
            assert selected[-1][1]['prompt_profile_id'] == agent_id + '.default'
            with sessions() as db:
                row = db.get(Account, account_id)
                assert (row.enable_rule_aware == 'true') == (agent_id == 'core.rule-aware')
    assert [item[0] for item in selected] == agent_ids
    engine.dispose()
