"""ASGI entrypoint.

All assembly lives in ``benchmark.bootstrap.app.create_app``; schema
migration, seeding, credential migration and background-task startup run
inside the app lifespan (``benchmark.bootstrap.runtime``), not at
import time. Importing this module must stay free of Redis/Docker/DB-write
side effects (M18 acceptance).
"""

from config.logging_config import setup_logging

# Initialize logging configuration before importing app modules.
setup_logging()

from benchmark.bootstrap.app import create_app  # noqa: E402
from benchmark.bootstrap.runtime import StartupMode  # noqa: E402

# Production default is FULL; tests build their own app with an explicit
# mode via create_app(settings, mode) instead of importing this one.
app = create_app(mode=StartupMode.FULL)
