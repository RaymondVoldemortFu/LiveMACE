from __future__ import annotations

import inspect

from api import (
    account_routes,
    agent_routes,
    compliance_routes,
    evaluation_routes,
    extension_routes,
    memory_routes,
    order_routes,
    ranking_routes,
    ws,
)
from benchmark.bootstrap.app import create_app
from benchmark.bootstrap.runtime import StartupMode


M14_OPERATIONS = {
    ("get", "/api/extensions"),
    ("get", "/api/extensions/agents"),
    ("get", "/api/extensions/toolsets"),
    ("get", "/api/extensions/prompts"),
    ("get", "/api/extensions/components/{component_id}/schema"),
    ("get", "/api/account/{account_id}/runtime-config"),
    ("post", "/api/account/{account_id}/runtime-config/validate"),
    ("put", "/api/account/{account_id}/runtime-config"),
}


def test_m14_operations_are_registered_with_declared_success_schemas():
    schema = create_app(mode=StartupMode.NO_BACKGROUND).openapi()

    assert M14_OPERATIONS <= {
        (method, path)
        for path, operations in schema["paths"].items()
        for method in operations
    }
    for method, path in M14_OPERATIONS:
        operation = schema["paths"][path][method]
        success = operation["responses"]["200"]
        assert success["content"]["application/json"]["schema"]


def test_g8_route_adapters_do_not_own_sessions_or_query_the_orm():
    for module in (
        account_routes,
        agent_routes,
        compliance_routes,
        evaluation_routes,
        extension_routes,
        memory_routes,
        order_routes,
        ranking_routes,
        ws,
    ):
        source = inspect.getsource(module)
        assert "SessionLocal" not in source
        assert ".query(" not in source
        assert ".commit(" not in source
        assert ".rollback(" not in source


def test_removed_unregistered_legacy_routes_are_not_in_openapi():
    paths = create_app(mode=StartupMode.NO_BACKGROUND).openapi()["paths"]

    assert "/api/users/login" not in paths
    assert "/api/users/profile" not in paths
    assert "/api/accounts/" not in paths
