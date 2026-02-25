import hmac
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.api.routes import router
from app.config import settings

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="RadianceFleet",
    description=(
        "Open source maritime anomaly detection for shadow fleet triage. "
        "This is a triage tool, not a legal determination engine."
    ),
    version="0.1.0",
    license_info={"name": "Apache-2.0"},
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

app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}
