from __future__ import annotations

import ast
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parents[2] / "benchmark" / "agents"


def test_public_agent_framework_does_not_import_builtin_agents_or_runtime_layers():
    forbidden = {"api", "database", "services", "fastapi", "sqlalchemy"}
    violations = []
    for path in AGENT_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module_names = []
            if isinstance(node, ast.Import):
                module_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_names = [node.module]
            for module_name in module_names:
                if module_name.split(".", 1)[0] in forbidden:
                    violations.append(f"{path.name}: {module_name}")
    assert violations == []

