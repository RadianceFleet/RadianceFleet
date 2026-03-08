"""Aggregator router — includes all sub-routers for the /api/v1 prefix."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes_vessels import router as vessels_router
from app.api.routes_alerts import router as alerts_router
from app.api.routes_detection import router as detection_router
from app.api.routes_admin import router as admin_router
from app.api.routes_health import router as health_router
from app.api.routes_sse import router as sse_router

# Backward-compatible re-exports (external code imports these from app.api.routes)
from app.api._helpers import _get_coverage_quality  # noqa: F401
from app.api._helpers import _audit_log  # noqa: F401
from app.api._helpers import _validate_date_range  # noqa: F401
from app.api._helpers import _check_upload_size  # noqa: F401
from app.api._helpers import limiter  # noqa: F401

router = APIRouter()
router.include_router(vessels_router)
router.include_router(alerts_router)
router.include_router(detection_router)
router.include_router(admin_router)
router.include_router(health_router)
router.include_router(sse_router)
