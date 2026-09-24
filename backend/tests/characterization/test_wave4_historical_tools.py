from services.evaluation.run_tool_use_eval import _evaluate_trace_job, summarize_scores


def evaluate(name, unavailable):
    return _evaluate_trace_job('trace', [{'role': 'assistant', 'tool_calls': [
        {'function': {'name': name, 'arguments': '{}'}}
    ]}], {'account_id': 1}, {}, {}, '', '', '', '', True, unavailable)['objective_metrics']['summary']


def test_missing_historical_schema_is_not_scored_as_hallucination():
    result = evaluate('example.old', [{'requested_name': 'example.old', 'status': 'unavailable'}])
    assert result['schema_status'] == 'unavailable'
    assert result['unavailable_calls'] == 1
    assert result['hallucination_rate'] is None
    assert result['invalid_or_noop_rate'] is None
    assert result['total_tool_calls'] == 1
    assert result['tool_calls_per_step'] == 1


def test_unavailable_meta_tool_does_not_use_static_fallback_schema():
    assert evaluate('select_tools', [{'requested_name': 'select_tools'}])['invalid_param_calls'] is None


def test_unknown_call_is_still_a_hallucination_when_evaluable():
    assert evaluate('invented_tool', [])['hallucination_rate'] == 1


def test_unavailable_scores_neither_bias_aggregate_nor_become_zero():
    assert summarize_scores([None, 0.25]) == dict(mean=.25, median=.25, variance=0., evaluated_count=1, unavailable_count=1)
    assert summarize_scores([None]) == dict(mean=None, median=None, variance=None, evaluated_count=0, unavailable_count=1)
    assert summarize_scores([.25, .75]) == dict(mean=.5, median=.5, variance=.0625)


def test_recorded_tool_not_found_remains_a_hallucination_even_when_installed_today():
    from contextlib import contextmanager
    from types import SimpleNamespace
    from benchmark.application.evaluation import EvaluationService, EvaluateTraceRequest
    from benchmark.extensions import ExtensionSettings, build_extension_runtime
    from services.evaluation.run_tool_use_eval import trace_schema_inputs

    name = 'core.market_snapshot'
    steps = [SimpleNamespace(account_id=1, role='assistant', tool_calls=[{'function': {'name':name,'arguments':'{}'}}])]
    events = [SimpleNamespace(account_id=1, decision_round_id='round', event_type='tool.denied', payload={'tool_name':name,'tool_version':'unknown','metadata':{'error_code':'TOOL_NOT_FOUND'}})]
    @contextmanager
    def unit():
        yield SimpleNamespace(accounts=SimpleNamespace(get=lambda _:SimpleNamespace(id=1,name='test')), traces=SimpleNamespace(list_by_trace_id=lambda _:steps,list_runtime_events=lambda _:events))
    result = EvaluationService(unit, build_extension_runtime(ExtensionSettings())).evaluate_trace(EvaluateTraceRequest(1,'trace'))
    assert result.tools[0].status == 'not_found'
    schemas, unavailable, missing = trace_schema_inputs(result)
    scored = _evaluate_trace_job('trace',[{'role':'assistant','tool_calls':steps[0].tool_calls}],{},schemas,{},'','','','',True,unavailable,missing)
    assert scored['objective_metrics']['summary']['hallucination_rate'] == 1
    assert scored['objective_metrics']['summary']['unavailable_calls'] == 0


def test_public_tool_legacy_alias_uses_recorded_version():
    from types import SimpleNamespace
    from benchmark.application.evaluation.tool_schema import resolve_recorded_tool
    from benchmark.contracts import ComponentNotFoundError
    entry = SimpleNamespace(extension=SimpleNamespace(version='1.0.0'), spec=SimpleNamespace(input_schema={'type':'object'}))
    def get(name):
        if name == 'public.example_api': return entry
        raise ComponentNotFoundError('missing')
    runtime = SimpleNamespace(tools=SimpleNamespace(get=get))
    result = resolve_recorded_tool(runtime, 'example_api', {'public.example_api':'1.0.0'}, {})
    assert result['status'] == 'available'
    assert result['name'] == 'public.example_api'
    assert result['requested_name'] == 'example_api'
