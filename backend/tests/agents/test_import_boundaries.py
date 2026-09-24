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


def test_builtin_and_runtime_agents_do_not_import_prompt_constants():
    backend = Path(__file__).resolve().parents[2]
    targets = [
        backend / "benchmark" / "builtin" / "agents",
        backend / "services" / "agent" / "react.py",
        backend / "services" / "agent" / "multi_agent.py",
        backend / "services" / "agent" / "multi_agent_advanced.py",
        backend / "services" / "agent" / "sub_agents" / "search_agent.py",
        backend / "services" / "agent" / "rule_aware" / "rule_aware_agent.py",
        backend / "services" / "agent" / "rule_aware" / "llm_auditor.py",
    ]
    forbidden_names = {
        "MANAGER_PROMPT",
        "TRADING_AGENT_PROMPT",
        "NEWS_AGENT_PROMPT",
        "CODER_AGENT_PROMPT",
        "SUB_AGENT_SYSTEM_PROMPT",
        "Advanced_MANAGER_PROMPT",
        "ANALYST_AGENT_PROMPT",
        "CRITIC_AGENT_PROMPT",
        "ADVANCED_EXECUTION_PROMPT",
        "RULE_AWARE_SYSTEM_PROMPT",
        "RULE_AWARE_REMINDER_PROMPT",
        "AUDIT_SYSTEM_PROMPT",
    }
    violations = []
    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files.extend(target.glob("*.py"))
        else:
            files.append(target)
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name in forbidden_names:
                    violations.append(f"{path.name}: {alias.name}")
    assert violations == []

