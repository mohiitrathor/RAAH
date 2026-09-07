from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.config import FRONTEND_DIR
from api.settings import settings
from api.observability import setup_structured_logging, ObservabilityMiddleware, get_request_id
from api.observability.metrics import metrics_collector
from api.dependencies import manager
from api.persistence.bridge import persistence_bridge
from api.auth import (
    auth_router,
    require_authenticated_user,
    AuthenticatedUser,
    Permission,
    require_permission,
)
from api.routers import (
    dispatch,
    state,
    events,
    redirection,
    simulation,
    analytics,
    coordination,
    scenarios,
    drills,
    replay_analysis,
    post_incident,
    optimization,
    persistence,
    ingestion,
    decision_evidence,
)

# Initialize structured logging
setup_structured_logging(log_level=settings.log_level, log_format=settings.log_format)


# ==============================================================
# LIFESPAN
# ==============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup: Validate production settings, initialize the Simulator singleton,
    perform state recovery if enabled, and start persistence workers.

    Shutdown: Execute deterministic graceful shutdown with bounded timeout.
    """
    violations = settings.validate_production_settings()
    if violations:
        import logging as _l
        _logger = _l.getLogger("raah.lifespan")
        for v in violations:
            _logger.critical("PRODUCTION CONFIGURATION VIOLATION: %s", v)
        raise ValueError(f"Production configuration validation failed: {violations}")

    manager.initialize()
    import asyncio
    from api.realtime.broadcaster import broadcaster
    try:
        broadcaster.set_event_loop(asyncio.get_running_loop())
    except Exception:
        pass
    yield
    # Execute deterministic graceful shutdown with bounded timeout
    manager.shutdown(timeout_seconds=settings.request_timeout_seconds)


# ==============================================================
# APPLICATION
# ==============================================================

app = FastAPI(
    title=settings.app_name,
    description=(
        "AI/ML-powered dynamic ambulance dispatch "
        "and hospital redirection system."
    ),
    version=settings.app_version,
    lifespan=lifespan,
)


# ==============================================================
# MIDDLEWARE
# ==============================================================

# Request correlation ID & structured access logging
app.add_middleware(ObservabilityMiddleware)

# CORS Whitelist from centralized settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.effective_cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)


# ==============================================================
# ROUTERS
# ==============================================================

# Authentication & Token Issuance (Public endpoints)
app.include_router(
    auth_router,
)

# Authenticated Operational Routers
auth_dep = [Depends(require_authenticated_user())]

app.include_router(
    dispatch.router,
    prefix="/dispatch",
    tags=["Dispatch"],
    dependencies=auth_dep,
)

app.include_router(
    state.router,
    prefix="/state",
    tags=["State"],
    dependencies=auth_dep,
)

app.include_router(
    events.router,
    prefix="/events",
    tags=["Events"],
    dependencies=auth_dep,
)

app.include_router(
    redirection.router,
    prefix="/redirect",
    tags=["Redirection"],
    dependencies=auth_dep,
)

app.include_router(
    simulation.router,
    prefix="/simulation",
    tags=["Simulation"],
    dependencies=auth_dep,
)

app.include_router(
    analytics.router,
    prefix="/analytics",
    tags=["Analytics"],
    dependencies=auth_dep,
)

app.include_router(
    coordination.router,
    dependencies=auth_dep,
)

app.include_router(
    scenarios.router,
    dependencies=auth_dep,
)

app.include_router(
    drills.router,
    dependencies=auth_dep,
)

app.include_router(
    replay_analysis.router,
    dependencies=auth_dep,
)

app.include_router(
    post_incident.router,
    dependencies=auth_dep,
)

app.include_router(
    optimization.router,
    dependencies=auth_dep,
)

app.include_router(
    persistence.router,
    dependencies=auth_dep,
)

app.include_router(
    ingestion.router,
    dependencies=auth_dep,
)

# Realtime Server-Sent Events Stream (M13 Phase 1)
from api.realtime import realtime_router
app.include_router(
    realtime_router,
)

# Decision Evidence / Explanation API (M13 Phase 2)
app.include_router(
    decision_evidence.router,
    prefix="/decision-evidence",
    tags=["Decision Evidence"],
    dependencies=auth_dep,
)


# ==============================================================
# HEALTH
# ==============================================================

@app.get(
    "/health",
    tags=["Health"],
    summary="Health check (legacy backwards-compatible)",
    description=(
        "Returns server status, current simulation time, "
        "and real-time mode status."
    ),
)
def health():

    return {
        "status": "ok",
        "time": manager.simulator.state.current_time,
        "realtime_running": manager.is_realtime_running,
    }


@app.get(
    "/health/live",
    tags=["Health"],
    summary="Process liveness probe",
    description="Cheap probe confirming the API process is alive and accepting traffic.",
)
def health_live():
    return {
        "status": "ALIVE",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get(
    "/health/ready",
    tags=["Health"],
    summary="Application readiness probe",
    description="Deep probe checking simulator, database, and background engine readiness.",
)
def health_ready():
    is_ready, checks = manager.check_readiness()
    payload = {
        "status": "READY" if is_ready else "NOT_READY",
        "ready": is_ready,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
    if not is_ready:
        return JSONResponse(status_code=503, content=payload)
    return payload


# ==============================================================
# OPERATIONAL METRICS
# ==============================================================

@app.get(
    "/metrics",
    tags=["Observability"],
    summary="Operational telemetry & metrics",
    description="Returns aggregated metrics for requests, dispatch latencies, errors, and queues.",
)
def get_metrics(
    user: AuthenticatedUser = Depends(require_permission(Permission.VIEW_LIVE)),
):
    return metrics_collector.get_snapshot()


# ==============================================================
# STANDARDIZED API ERROR HANDLERS
# ==============================================================

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Standardized HTTP error contract preserving status codes and auth headers."""
    headers = getattr(exc, "headers", None) or {}
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "HTTP_ERROR",
            "detail": exc.detail,
            "status_code": exc.status_code,
            "request_id": get_request_id(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Clean validation error contract without leaking internal file paths."""
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "detail": exc.errors(),
            "status_code": 422,
            "request_id": get_request_id(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Fallback error handler preventing stack trace or secret leakage."""
    _logger = logging.getLogger("raah.api.error")
    _logger.error(
        "Unhandled API operational exception on %s %s: %s",
        request.method,
        request.url.path,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected operational error occurred.",
            "status_code": 500,
            "request_id": get_request_id(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# ==============================================================
# DASHBOARD STATIC FILES
# ==============================================================

if FRONTEND_DIR.exists():
    app.mount(
        "/dashboard",
        StaticFiles(directory=str(FRONTEND_DIR), html=True),
        name="frontend",
    )

