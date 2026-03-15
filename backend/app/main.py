import hmac
import logging
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

# ── Sentry Error Tracking (optional dep — no-op if sentry-sdk not installed) ──
if settings.SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            environment=settings.SENTRY_ENVIRONMENT,
            send_default_pii=False,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        )
        logger.info("Sentry error tracking enabled (env=%s)", settings.SENTRY_ENVIRONMENT)
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed. "
            "Install with: uv pip install 'radiancefleet[monitoring]'"
        )

# ── Prometheus Metrics (optional dep — gated on PROMETHEUS_ENABLED) ──
if getattr(settings, "PROMETHEUS_ENABLED", False):
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        _instrumentator = Instrumentator(
            should_instrument=lambda info: not info.request.url.path.startswith("/sse/"),
            excluded_handlers=["/metrics"],
        )
    except ImportError:
        _instrumentator = None
        logger.warning(
            "PROMETHEUS_ENABLED is set but prometheus-fastapi-instrumentator is not installed. "
            "Install with: uv pip install prometheus-fastapi-instrumentator"
        )
else:
    _instrumentator = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate risk_scoring.yaml at startup."""
    config_path = Path(settings.RISK_SCORING_CONFIG)
    if not config_path.exists():
        logger.warning("risk_scoring.yaml not found at %s — scoring will use defaults", config_path)
    else:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        required_sections = [
            "gap_duration",
            "spoofing",
            "metadata",
            "legitimacy",
            "dark_zone",
            "corridor",
        ]
        for section in required_sections:
            if section not in config:
                logger.warning(
                    "Missing config section '%s' in risk_scoring.yaml — scoring may use defaults",
                    section,
                )

    # Create tables if they don't exist (essential for fresh deployments).
    # Retry on transient DNS/connection errors (Railway internal DNS may lag).
    import asyncio

    from app.database import init_db

    for attempt in range(1, 6):
        try:
            init_db()
            logger.info("Database tables verified")
            break
        except Exception as exc:
            if attempt == 5:
                raise
            logger.warning("DB init attempt %d/5 failed (%s), retrying in %ds…", attempt, exc, attempt * 2)
            await asyncio.sleep(attempt * 2)

    # Start history backfill scheduler if enabled
    history_scheduler = None
    if settings.HISTORY_BACKFILL_ENABLED:
        try:
            from app.database import SessionLocal
            from app.modules.history_scheduler import HistoryScheduler

            history_scheduler = HistoryScheduler(db_factory=SessionLocal)
            history_scheduler.start()
            logger.info("History backfill scheduler started")
        except Exception as e:
            logger.warning("Failed to start history scheduler: %s", e)

    yield

    if history_scheduler is not None:
        history_scheduler.stop()


app = FastAPI(
    title="RadianceFleet",
    description=(
        "Open source maritime anomaly detection for shadow fleet triage.\n\n"
        "**Rate limits:** configurable per-IP (default 60/min, admin 120/min, viewer 30/min).\n\n"
        "**Terms:** Research and journalism use only. Outputs are anomaly indicators for human "
        "investigation, not legal determinations.\n\n"
        "**Deprecation policy:** Endpoints are not removed without at least one minor version "
        "of deprecation notice. Deprecated endpoints return a `Deprecation` response header."
    ),
    version="3.4.0",
    license_info={"name": "Apache-2.0"},
    lifespan=lifespan,
    openapi_tags=[
        {"name": "alerts", "description": "AIS gap alerts, scoring, and triage"},
        {"name": "vessels", "description": "Vessel search, detail, and ownership"},
        {
            "name": "detection",
            "description": "Gap, spoofing, loitering, STS detection triggers and results",
        },
        {"name": "corridors", "description": "Corridor management and activity analysis"},
        {"name": "fleet", "description": "Fleet-level owner clusters and alerts"},
        {"name": "merge", "description": "Merge candidates and identity resolution"},
        {"name": "watchlist", "description": "Watchlist management and import"},
        {"name": "hunt", "description": "Named vessel hunt workflow (FR9)"},
        {"name": "coverage", "description": "AIS coverage quality regions"},
        {"name": "scoring", "description": "Risk scoring engine operations"},
        {"name": "export", "description": "Evidence export (cards, gov packages, PDF)"},
        {"name": "verification", "description": "Paid verification budget and triggers"},
        {"name": "admin", "description": "Audit log, ingestion, admin operations"},
        {"name": "dashboard", "description": "Dashboard statistics"},
    ],
)

# CORS — origins from settings (supports comma-separated env var)
cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-API-Key", "X-Requested-With"],
)
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    return response


class SlowQueryMiddleware(BaseHTTPMiddleware):
    """Log requests that take longer than 500ms."""

    async def dispatch(self, request: Request, call_next):
        import time

        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000
        if elapsed_ms > 500:
            logger.warning(
                "Slow request: %s %s took %.0fms",
                request.method,
                request.url.path,
                elapsed_ms,
            )
        return response


app.add_middleware(SlowQueryMiddleware)


# API key authentication middleware
class APIKeyMiddleware(BaseHTTPMiddleware):
    """Simple API key check. If RADIANCEFLEET_API_KEY is unset, all requests pass."""

    async def dispatch(self, request: Request, call_next):
        if settings.RADIANCEFLEET_API_KEY:
            path = request.url.path
            # Only require API key for /api/* endpoints.
            # Exempt: SPA frontend, static assets, health, docs, metrics.
            if path.startswith("/api/"):
                api_key = request.headers.get("X-API-Key")
                if not hmac.compare_digest(api_key or "", settings.RADIANCEFLEET_API_KEY):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or missing API key"},
                    )
        return await call_next(request)


app.add_middleware(APIKeyMiddleware)

# Rate limiting — configurable per-IP limits (single shared instance from _helpers)
from app.api._helpers import limiter  # noqa: E402

app.state.limiter = limiter


async def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    retry_after = getattr(exc, "retry_after", 60)
    return JSONResponse(
        status_code=429,
        content={"error": "Rate limit exceeded", "retry_after": retry_after},
        headers={"Retry-After": str(retry_after)},
    )


app.add_exception_handler(RateLimitExceeded, custom_rate_limit_handler)

app.include_router(router, prefix="/api/v1")

# Expose /metrics for Prometheus scraping
if _instrumentator is not None:
    _instrumentator.instrument(app).expose(app, endpoint="/metrics")


@app.get("/health")
def health() -> dict:
    from app.modules.circuit_breakers import get_circuit_states

    return {"status": "ok", "version": "3.4.0", "circuit_breakers": get_circuit_states()}


# ── Structured error handlers ─────────────────────────────────────────────────


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"error": "Validation error", "detail": str(exc)})


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    logger.warning("IntegrityError on %s %s: %s", request.method, request.url.path, exc.orig)
    return JSONResponse(
        status_code=409,
        content={"error": "Conflict", "detail": "Conflict: the record may already exist"},
    )


@app.exception_handler(Exception)
async def general_error_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception on %s %s:\n%s",
        request.method,
        request.url.path,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": "An unexpected error occurred."},
    )


# ── Static files + SPA fallback ──────────────────────────────────────────────
_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    _assets_dir = _static_dir / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """Serve index.html for any path not matched by API/health/docs routes."""
        if full_path.startswith(("api/", "health", "docs", "openapi.json", "redoc", "metrics")):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        # Serve actual static files (e.g. vite.svg) if they exist
        candidate = _static_dir / full_path
        if full_path and candidate.is_file() and _static_dir in candidate.resolve().parents:
            return FileResponse(str(candidate))
        index = _static_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse(status_code=404, content={"detail": "Frontend not built"})
