from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys

from benchmark.extensions import MANIFEST_FILENAME

BACKEND_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_ROOT = BACKEND_ROOT / "benchmark" / "extensions"


def test_cli_json_failure_has_stable_exit_code(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmark.extensions.validate",
            str(tmp_path),
            "--json",
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["valid"] is False
    assert payload["errors"]


def test_manifest_filename_is_benchmark_scoped():
    assert MANIFEST_FILENAME == "alpha-arena-extension.yaml"


def test_extension_runtime_does_not_import_application_layers():
    forbidden = {"api", "database", "services", "fastapi", "sqlalchemy"}
    violations = []
    for path in EXTENSION_ROOT.glob("*.py"):
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
