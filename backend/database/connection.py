import os

import dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

# Load local .env for direct python/uv runs (does not override exported env vars).
dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True), override=False)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data.db").strip()


def _ensure_mysql_database_exists(database_url: str) -> None:
    url = make_url(database_url)
    if not str(url.drivername).startswith("mysql"):
        return

    db_name = (url.database or "").strip()
    if not db_name:
        return

    # If target DB is already reachable, do nothing.
    probe_engine = create_engine(url, pool_pre_ping=True)
    try:
        with probe_engine.connect():
            return
    except OperationalError:
        pass
    finally:
        probe_engine.dispose()

    # Connect without selecting a default DB, then create target DB if missing.
    # This avoids requiring access to `mysql` system database.
    admin_url = url.set(database="")
    admin_engine = create_engine(admin_url, pool_pre_ping=True)
    try:
        safe_db_name = db_name.replace("`", "``")
        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{safe_db_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    except OperationalError as exc:
        raise RuntimeError(
            f"Failed to auto-create MySQL database '{db_name}'. "
            "Ensure the DB user has CREATE privilege or pre-create the database."
        ) from exc
    finally:
        admin_engine.dispose()


_ensure_mysql_database_exists(DATABASE_URL)

if DATABASE_URL.startswith("sqlite"):
    # SQLite is kept for local development; NullPool avoids QueuePool timeout under bursts.
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
else:
    # Production DB pool tuning for high-concurrency agent workloads.
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=max(1, int(os.getenv("DB_POOL_SIZE", "50"))),
        max_overflow=max(0, int(os.getenv("DB_MAX_OVERFLOW", "50"))),
        pool_timeout=max(1, int(os.getenv("DB_POOL_TIMEOUT", "30"))),
        pool_recycle=max(60, int(os.getenv("DB_POOL_RECYCLE", "1800"))),
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
