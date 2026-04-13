from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database.models import AgentTrace


def test_agent_trace_columns_are_longtext_on_mysql():
    ddl = str(CreateTable(AgentTrace.__table__).compile(dialect=mysql.dialect()))
    ddl_upper = ddl.upper()

    assert "CONTENT LONGTEXT" in ddl_upper
    assert "TOOL_CALLS LONGTEXT" in ddl_upper
    assert "TOOL_OUTPUT LONGTEXT" in ddl_upper
