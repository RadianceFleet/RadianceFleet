import hmac
import logging
import sys
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware
from app.api.routes import router
from app.config import settings

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate risk_scoring.yaml at startup."""
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "risk_scoring.yaml"
    if not config_path.exists():
        logger.critical("FATAL: config/risk_scoring.yaml not found at %s", config_path)
        sys.exit(1)
    with open(config_path) as f:
        config = yaml.safe_load(f)
    required_sections = ["gap_duration", "spoofing", "metadata", "legitimacy", "dark_zone", "corridor"]
    for section in required_sections:
        if section not in config:
            logger.warning("Missing config section '%s' in risk_scoring.yaml — scoring may use defaults", section)
    yield


app = FastAPI(
    title="RadianceFleet",
    description=(
        "Open source maritime anomaly detection for shadow fleet triage. "
        "This is a triage tool, not a legal determination engine."
    ),
    version="0.1.0",
    license_info={"name": "Apache-2.0"},
    lifespan=lifespan,
)

# CORS — origins from settings (supports comma-separated env var)
cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# API key authentication middleware
class APIKeyMiddleware(BaseHTTPMiddleware):
    """Simple API key check. If RADIANCEFLEET_API_KEY is unset, all requests pass."""

    async def dispatch(self, request: Request, call_next):
        if settings.RADIANCEFLEET_API_KEY is not None:
            # Allow health check and OpenAPI docs without auth
            if request.url.path not in ("/health", "/docs", "/openapi.json", "/redoc"):
                api_key = request.headers.get("X-API-Key")
                if not hmac.compare_digest(api_key or "", settings.RADIANCEFLEET_API_KEY):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or missing API key"},
                    )
        return await call_next(request)


app.add_middleware(APIKeyMiddleware)

# Rate limiting — 60/min reads, 10/min detection/mutation endpoints
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(router, prefix="/api/v1")


# ── Structured error handlers ─────────────────────────────────────────────────

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"error": "Validation error", "detail": str(exc)})


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    return JSONResponse(status_code=409, content={"error": "Conflict", "detail": str(exc.orig) if exc.orig else str(exc)})


@app.exception_handler(Exception)
async def general_error_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s %s:\n%s", request.method, request.url.path, traceback.format_exc())
    return JSONResponse(status_code=500, content={"error": "Internal server error", "detail": "An unexpected error occurred."})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}
