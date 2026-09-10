"""
RAAH Structured Observability — HTTP Middleware
===============================================

Provides request correlation ID injection, latency tracking, and structured request/response
logging for FastAPI.
"""

import time
import uuid
import logging
from contextvars import ContextVar
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Context variable for holding active correlation ID across async and threaded tasks
_request_id_ctx: ContextVar[str] = ContextVar("raah_request_id", default="")

logger = logging.getLogger("raah.observability.http")


def get_request_id() -> str:
    """Retrieve the current request correlation ID from context."""
    return _request_id_ctx.get()


def set_request_id(request_id: str) -> None:
    """Set the current request correlation ID in context."""
    _request_id_ctx.set(request_id)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """
    HTTP middleware that:
    1. Extracts or generates a unique correlation ID per request (X-Request-ID).
    2. Measures exact wall-clock request processing duration.
    3. Emits structured log events for request completion and unhandled errors.
    4. Propagates the X-Request-ID header in the HTTP response.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Extract incoming correlation ID or generate UUID4
        req_id = (
            request.headers.get("X-Request-ID")
            or request.headers.get("X-Correlation-ID")
            or str(uuid.uuid4())
        )
        set_request_id(req_id)

        start_time = time.perf_counter()
        method = request.method
        path = request.url.path
        client_host = request.client.host if request.client else "unknown"

        # Process request
        try:
            response: Response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            # Attach correlation ID to response headers
            response.headers["X-Request-ID"] = req_id

            # Record operational metrics
            try:
                from api.observability.metrics import metrics_collector
                metrics_collector.record_http_request(method, path, response.status_code, duration_ms)
                if path == "/ingestion/cad/intake" and response.status_code == 422:
                    metrics_collector.record_cad_intake_validation_failure()
            except Exception:
                pass

            # Skip high-frequency static asset noise in structured access log if desired
            if not path.startswith("/static"):
                logger.info(
                    f"{method} {path} -> {response.status_code} ({duration_ms:.2f}ms)",
                    extra={
                        "http_method": method,
                        "http_path": path,
                        "http_status": response.status_code,
                        "duration_ms": round(duration_ms, 2),
                        "client_ip": client_host,
                    },
                )
            return response

        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(
                f"Unhandled exception during {method} {path} ({duration_ms:.2f}ms): {exc}",
                exc_info=True,
                extra={
                    "http_method": method,
                    "http_path": path,
                    "duration_ms": round(duration_ms, 2),
                    "client_ip": client_host,
                    "error": str(exc),
                },
            )
            raise
