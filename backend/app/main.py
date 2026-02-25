from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.config import settings
import logging

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}
