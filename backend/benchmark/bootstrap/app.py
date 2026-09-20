"""App factory (M18): assembly only — middleware, routes, static, lifespan.

``create_app`` never creates tables, never connects Redis, never starts
threads; all of that happens in the lifespan via ``bootstrap_runtime``
when the server actually starts. Importing this module (or a module that
calls ``create_app``) therefore has no runtime side effects.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import List, Optional

import anyio
from fastapi import FastAPI, Request

from benchmark.bootstrap.runtime import (
    BootstrapContext,
    StartupMode,
    bootstrap_runtime,
    shutdown_runtime,
    RuntimeShutdownError,
)

logger = logging.getLogger(__name__)


@dataclass
class AppSettings:
    title: str = "Crypto Paper Trading API"
    cors_allow_origins: List[str] = field(default_factory=lambda: ["*"])
    static_dir: Optional[str] = None  # default: backend/static
    startup_cleanup_timeout_seconds: float = 10.0
    shutdown_cleanup_timeout_seconds: float = 90.0

    def resolved_static_dir(self) -> str:
        if self.static_dir is not None:
            return self.static_dir
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(backend_dir, "static")


def create_app(
    settings: Optional[AppSettings] = None,
    mode: StartupMode = StartupMode.FULL,
) -> FastAPI:
    settings = settings if settings is not None else AppSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from benchmark.bootstrap.runtime import RuntimeBootstrapError

        if mode == StartupMode.SCHEMA_ONLY:
            raise RuntimeError(
                "SCHEMA_ONLY is for scripts/tests via bootstrap_runtime_sync; "
                "an HTTP app must use FULL or NO_BACKGROUND"
            )
        try:
            handle = await bootstrap_runtime(BootstrapContext(mode=mode))
        except RuntimeBootstrapError as exc:
            # Startup never reaches the lifespan yield/finally, so explicitly
            # take ownership of the retryable handle before propagating the
            # startup failure to ASGI.
            app.state.runtime_handle = exc.handle
            # External ASGI cancellation must not steal ownership mid-cleanup.
            # The retry budget is bounded, but an in-flight stop callback is
            # never abandoned: each shutdown_runtime call runs to completion
            # (stop callbacks themselves are bounded) before the next retry.
            with anyio.CancelScope(shield=True):
                retry_delay = 0.05
                deadline = (
                    time.monotonic()
                    + max(settings.startup_cleanup_timeout_seconds, 0.0)
                )
                cleanup_failures = dict(exc.cleanup_failures)
                while True:
                    try:
                        await shutdown_runtime(exc.handle)
                    except Exception as cleanup_error:
                        failures = getattr(cleanup_error, "failures", None)
                        if failures:
                            cleanup_failures = dict(failures)
                        else:
                            cleanup_failures = {
                                "runtime": (
                                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                                )
                            }
                        logger.exception(
                            "runtime startup cleanup remains incomplete; retrying"
                        )
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            cleanup_failures["runtime"] = (
                                "startup cleanup deadline exceeded"
                            )
                            raise RuntimeBootstrapError(
                                exc.start_error,
                                exc.handle,
                                cleanup_failures,
                            ) from exc
                        await anyio.sleep(min(retry_delay, remaining))
                        retry_delay = min(retry_delay * 2, 1.0)
                    else:
                        raise exc.start_error from exc
        app.state.runtime_handle = handle
        try:
            yield
        finally:
            # Stop callbacks can report a still-draining worker. Keep ownership
            # and retry until cleanup completes instead of exiting ASGI early.
            with anyio.CancelScope(shield=True):
                deadline = time.monotonic() + max(settings.shutdown_cleanup_timeout_seconds, 0)
                while True:
                    try:
                        await shutdown_runtime(handle)
                    except RuntimeShutdownError:
                        if time.monotonic() >= deadline:
                            raise
                        await anyio.sleep(0.1)
                    else:
                        break

    app = FastAPI(title=settings.title, lifespan=lifespan)
    app.state.startup_mode = mode

    _register_middleware(app, settings)
    _register_health(app)
    _register_static(app, settings)
    _register_routes(app)
    _register_spa(app, settings)
    return app


def _register_middleware(app: FastAPI, settings: AppSettings) -> None:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _register_health(app: FastAPI) -> None:
    @app.get("/api/health")
    async def health_check(request: Request):
        handle = getattr(request.app.state, "runtime_handle", None)
        if handle is None:
            return {"status": "starting", "message": "Trading API is starting"}
        return {
            "status": "healthy" if handle.is_ready() else "degraded",
            "message": "Trading API is running",
            "services": handle.health(),
        }

    @app.get("/api/ready")
    def readiness_check(request: Request):
        from fastapi.responses import JSONResponse
        handle = getattr(request.app.state, "runtime_handle", None)
        ready = bool(handle is not None and handle.is_ready())
        dependencies = {}
        if ready and os.getenv("WAVE3_PRODUCTION", "").lower() == "true":
            from .readiness import production_dependencies
            dependencies = production_dependencies()
            ready = all(value == "ready" for value in dependencies.values())
        return JSONResponse(status_code=200 if ready else 503, content={
            "ready": ready,
            "status": "ready" if ready else "not_ready",
            "services": handle.health() if handle is not None else {},
            "dependencies": dependencies,
        })


def _register_static(app: FastAPI, settings: AppSettings) -> None:
    from fastapi.staticfiles import StaticFiles

    static_dir = settings.resolved_static_dir()
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        assets_dir = os.path.join(static_dir, "assets")
        if os.path.exists(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


def _register_routes(app: FastAPI) -> None:
    from api.account_routes import router as account_router
    from api.agent_routes import router as agent_router
    from api.compliance_routes import router as compliance_router
    from api.config_routes import router as config_router
    from api.crypto_routes import router as crypto_router
    from api.evaluation_routes import router as evaluation_router
    from api.extension_routes import router as extension_router
    from api.market_data_routes import router as market_data_router
    from api.memory_routes import router as memory_router
    from api.order_routes import router as order_router
    from api.ranking_routes import router as ranking_router
    from api.rule_routes import router as rule_router
    from api.ws import websocket_endpoint

    app.include_router(market_data_router)
    app.include_router(order_router)
    app.include_router(account_router)
    app.include_router(config_router)
    app.include_router(ranking_router)
    app.include_router(crypto_router)
    app.include_router(agent_router)
    app.include_router(memory_router)
    app.include_router(rule_router)
    app.include_router(compliance_router)
    app.include_router(evaluation_router)
    app.include_router(extension_router)
    app.websocket("/ws")(websocket_endpoint)


def _register_spa(app: FastAPI, settings: AppSettings) -> None:
    from fastapi.responses import FileResponse

    static_dir = settings.resolved_static_dir()

    @app.get("/")
    async def serve_root():
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"message": "Frontend not built yet"}

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if (
            full_path.startswith("api")
            or full_path.startswith("static")
            or full_path.startswith("docs")
            or full_path.startswith("openapi.json")
        ):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"message": "Frontend not built yet"}
