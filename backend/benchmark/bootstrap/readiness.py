"""Bounded live dependency probes for the isolated production deployment."""

import os


def production_dependencies():
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool
    from redis import Redis
    import docker
    from database.safety import validate_database_target

    status = {}
    engine = redis_client = docker_client = None
    try:
        url = os.environ["DATABASE_URL"]
        validate_database_target(url)
        engine = create_engine(
            url,
            poolclass=NullPool,
            connect_args={
                "connect_timeout": 2,
                "read_timeout": 2,
                "write_timeout": 2,
            },
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        status["mysql"] = "ready"
    except Exception:
        status["mysql"] = "unavailable"
    finally:
        if engine is not None:
            engine.dispose()
    try:
        redis_client = Redis.from_url(
            os.environ["TOOL_CACHE_REDIS_URL"],
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        status["redis"] = "ready" if redis_client.ping() else "unavailable"
    except Exception:
        status["redis"] = "unavailable"
    finally:
        if redis_client is not None:
            redis_client.close()
    try:
        docker_client = docker.from_env(timeout=2)
        status["docker"] = "ready" if docker_client.ping() else "unavailable"
    except Exception:
        status["docker"] = "unavailable"
    finally:
        if docker_client is not None:
            docker_client.close()
    return status
