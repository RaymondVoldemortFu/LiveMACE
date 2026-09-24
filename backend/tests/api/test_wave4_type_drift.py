"""Regenerate every HTTP/WS DTO, including field types and required/null semantics."""
from copy import deepcopy
import pytest

from benchmark.bootstrap.app import create_app
from benchmark.bootstrap.runtime import StartupMode
from scripts.generate_frontend_types import OUTPUT, contracts, render


def test_generated_dtos_match_all_contracts():
    assert OUTPUT.read_text() == render(contracts()), 'Run scripts/generate_frontend_types.py'


@pytest.mark.parametrize('mutation', ['type', 'required', 'nullable', 'nested', 'enum'])
def test_drift_detection_includes_type_and_nested_constraints(mutation):
    schemas = contracts()
    changed = deepcopy(schemas)
    schema = changed['TradingAccount']
    if mutation == 'type':
        schema['properties']['id'] = {'type': 'string'}
    elif mutation == 'required':
        schema['required'].remove('id')
    elif mutation == 'nullable':
        schema['properties']['id'] = {'anyOf': [{'type': 'integer'}, {'type': 'null'}]}
    elif mutation == 'nested':
        changed['ComplianceAggregate']['properties']['gate_pass_rate'] = {'type': 'string'}
    else:
        schema['properties']['account_type'] = {'enum': ['AI', 'MANUAL']}
    assert render(changed) != render(schemas)


def test_control_plane_routes_publish_response_schemas():
    schema = create_app(mode=StartupMode.NO_BACKGROUND).openapi()
    prefixes = ('/api/evaluation/', '/api/compliance/', '/api/rules/', '/api/extensions')
    for path, operations in schema['paths'].items():
        if not path.startswith(prefixes):
            continue
        for operation in operations.values():
            if '200' in operation.get('responses', {}):
                assert operation['responses']['200']['content']['application/json']['schema'], path
