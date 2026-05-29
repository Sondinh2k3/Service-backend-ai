"""FastAPI entrypoint — chia router theo `service_role` (RLOps Lop 2 split).

`SERVICE_ROLE` (env):
  - `runtime` — chi serve inference + readiness + internal runtime reload.
  - `ops`     — chi serve quan tri bundle + sync API.
  - `all`     — chay ca 2 trong 1 process (dev/MVP).

Tach 2 container:
  - ai-runtime: SERVICE_ROLE=runtime, port 8000 (public + internal reload).
  - ai-ops:     SERVICE_ROLE=ops,     port 8002.
Cung image, cung volume Local Model Storage.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.core.config import get_settings
from src.core.exception import (
    AlgorithmException,
    algorithm_exception_handler,
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from src.core.logger import logger
from src.core.telemetry import TelemetryMiddleware
from src.db.base import init_db


def _attach_runtime_routers(app: FastAPI) -> None:
    from src.api import ai as ai_router
    from src.runtime import api as runtime_internal_api
    app.include_router(ai_router.router)
    app.include_router(runtime_internal_api.router)


def _attach_ops_routers(app: FastAPI) -> None:
    from src.api import internal_sync as sync_router
    from src.ops import api as ops_api
    app.include_router(sync_router.router)
    app.include_router(ops_api.router)


def _runtime_startup() -> None:
    """Preflight every network with an Active bundle. Fail-fast only in strict mode."""
    from src.bundles import networks_root, read_active_pointer
    from src.runtime.preflight import PreflightError, run_preflight

    settings = get_settings()
    root = networks_root()
    if not root.exists():
        return

    for net_dir in root.iterdir():
        if not net_dir.is_dir() or read_active_pointer(net_dir) is None:
            continue
        network_id = net_dir.name
        try:
            run_preflight(network_id)
        except PreflightError as e:
            msg = f"Preflight fail network={network_id}: {e}"
            if settings.ai_strict_mode:
                logger.error(msg)
                raise
            logger.warning(msg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    role = (settings.service_role or "all").lower()
    logger.info(
        f"Starting {settings.service_name} env={settings.app_env} "
        f"role={role} strict={settings.ai_strict_mode}"
    )
    init_db()

    if role in ("runtime", "all") and settings.startup_preflight:
        try:
            _runtime_startup()
        except Exception as e:
            logger.exception(f"Bundle preflight failed: {e}")

    # ai-ops auto-sync: listener (instant) + safety-net poller.
    if role in ("ops", "all"):
        try:
            from src.ops import auto_sync
            auto_sync.start()
        except Exception as e:
            logger.exception(f"Auto-sync start failed: {e}")

    yield

    if role in ("ops", "all"):
        try:
            from src.ops import auto_sync
            await auto_sync.stop()
        except Exception as e:
            logger.warning(f"Auto-sync stop failed: {e}")

    logger.info("Shutting down.")


def create_app() -> FastAPI:
    settings = get_settings()
    role = (settings.service_role or "all").lower()

    title_map = {
        "runtime": "AI Runtime Service",
        "ops": "AI Ops Service",
        "all": "AI Algorithm Service (combined)",
    }
    app = FastAPI(
        title=title_map.get(role, "AI Algorithm Service"),
        description=(
            "AI inference + RLOps Lop 2 (Edge Server). "
            "Tach 2 container: ai-runtime (inference) va ai-ops (model lifecycle)."
        ),
        version="2.2.0",
        lifespan=lifespan,
    )

    app.add_middleware(TelemetryMiddleware)

    app.add_exception_handler(AlgorithmException, algorithm_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    if role in ("runtime", "all"):
        _attach_runtime_routers(app)
    if role in ("ops", "all"):
        _attach_ops_routers(app)

    from src.observability.metrics import register_metrics
    register_metrics(app)

    @app.get("/health", tags=["health"])
    def health_check():
        return {"status": "ok", "role": role}

    @app.get("/ready", tags=["health"])
    def readiness_probe():
        from src.services.readiness_service import service_ready
        result = service_ready()
        result["role"] = role
        status_code = 200 if result.get("ready") else 503
        return JSONResponse(status_code=status_code, content=result)

    return app


app = create_app()
