from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BACKEND_ROOT / "alpha_arena" / "contracts"


def test_contract_modules_do_not_import_runtime_layers():
    forbidden = {"api", "database", "services", "repositories", "fastapi", "sqlalchemy"}
    violations = []
    for path in CONTRACT_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".", 1)[0] in forbidden:
                    violations.append(f"{path.name}: {name}")
    assert violations == []


def test_contract_import_has_no_application_side_effects():
    script = """
import json, sys
import alpha_arena.contracts
forbidden = sorted(name for name in sys.modules if name.split('.', 1)[0] in {'api','database','services','repositories','fastapi','sqlalchemy','redis','docker'})
print(json.dumps(forbidden))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "[]"


def test_hatch_wheel_packages_public_alpha_arena_namespace():
    pyproject = (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'packages = ["alpha_arena"]' in pyproject
    assert 'packages = ["main.py"]' not in pyproject

