"""Validate database targets before creating engines or running migrations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping
from urllib.parse import unquote

from sqlalchemy.engine import make_url


def validate_database_target(
    database_url: str, environ: Mapping[str, str] | None = None
) -> None:
    """Reject the preserved dataset and require the isolated production schema.

    This function opens no database. Errors deliberately omit DSN credentials.
    ``WAVE3_PRODUCTION=true`` requires an explicit MySQL target.
    """
    env = os.environ if environ is None else environ
    url = make_url(database_url)
    if env.get("WAVE3_PRODUCTION", "").lower() == "true":
        if url.get_backend_name() != "mysql" or url.database != "alpha_arena_wave3":
            raise ValueError(
                "WAVE3 production requires MySQL database alpha_arena_wave3"
            )
    if (
        url.get_backend_name() != "sqlite"
        or not url.database
        or url.database == ":memory:"
    ):
        return
    raw = unquote(url.database)
    if raw.startswith("file:"):
        raise ValueError("SQLite file URIs are not supported")
    target = Path(raw).expanduser().resolve()
    protected = (
        Path(
            env.get(
                "PROTECTED_DATABASE_PATH",
                str(Path(__file__).resolve().parents[2] / "alpha_arena_final.sqlite"),
            )
        )
        .expanduser()
        .resolve()
    )
    same_file = target.exists() and protected.exists() and target.samefile(protected)
    if target.name == "alpha_arena_final.sqlite" or target == protected or same_file:
        raise ValueError("The original alpha_arena_final.sqlite dataset is protected")
