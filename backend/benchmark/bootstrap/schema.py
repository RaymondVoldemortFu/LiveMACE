"""Schema bootstrap stage (M18): database existence, tables, startup migrations.

Moved out of ``main.py``. Idempotent: safe to run on every process start.
The migration list itself lives in ``database.migrations_startup`` (M19 /
RFC-0006); this stage only orchestrates it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


@dataclass
class SchemaReport:
    tables_created: bool = False
    migrations_applied: List[str] = field(default_factory=list)


def run_schema_bootstrap(engine: Optional[Engine] = None) -> SchemaReport:
    """Ensure database exists, create tables, apply startup migrations."""
    from database.connection import Base, DATABASE_URL, ensure_database_exists
    from database.connection import engine as default_engine
    from database.migrations_startup import run_startup_migrations

    engine = engine if engine is not None else default_engine
    report = SchemaReport()

    # MySQL only: auto-create the target database (moved here from
    # database/connection.py import time so importing the app never writes).
    if engine is default_engine:
        ensure_database_exists(DATABASE_URL)

    # Importing the declarative Base alone does not register model tables.
    # Schema bootstrap owns this ordering explicitly so a clean process does
    # not depend on an unrelated route or service importing database.models.
    import database.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    report.tables_created = True
    logger.info("schema bootstrap: tables ensured")

    report.migrations_applied = run_startup_migrations(engine)
    return report
