from __future__ import annotations

import ast
from pathlib import Path

PROMPT_ROOT = Path(__file__).resolve().parents[2] / "benchmark" / "prompts"


def test_prompt_runtime_does_not_import_application_or_agent_layers():
    forbidden = {"api", "database", "services", "agents", "fastapi", "sqlalchemy"}
    violations = []
    for path in PROMPT_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in forbidden:
                        violations.append(f"{path.name}: {alias.name}")
            if module and module.split(".", 1)[0] in forbidden:
                violations.append(f"{path.name}: {module}")
    assert violations == []
