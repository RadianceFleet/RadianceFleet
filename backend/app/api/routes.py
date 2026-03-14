"""Aggregator router — includes all sub-routers for the /api/v1 prefix."""

from __future__ import annotations

from fastapi import APIRouter

# Backward-compatible re-exports (external code imports these from app.api.routes)
from app.api._helpers import (
    _audit_log,  # noqa: F401
    _check_upload_size,  # noqa: F401
    _get_coverage_quality,  # noqa: F401
    _validate_date_range,  # noqa: F401
    limiter,  # noqa: F401
)
from app.api.routes_admin import router as admin_router
from app.api.routes_alerts import router as alerts_router
from app.api.routes_cases import router as cases_router
from app.api.routes_behavioral_baseline import router as behavioral_baseline_router
from app.api.routes_collaboration import router as collaboration_router
from app.api.routes_detection import router as detection_router
from app.api.routes_embed import router as embed_router
from app.api.routes_explainability import router as explainability_router
from app.api.routes_fp_tuning import router as fp_tuning_router
from app.api.routes_health import router as health_router
from app.api.routes_jamming_zones import router as jamming_zones_router
from app.api.routes_ownership_network import router as ownership_network_router
from app.api.routes_public import router as public_router
from app.api.routes_sse import router as sse_router
from app.api.routes_sts_hotspots import router as sts_hotspots_router
from app.api.routes_trajectory_pca import router as trajectory_pca_router
from app.api.routes_vessels import router as vessels_router

router = APIRouter()
router.include_router(vessels_router)
router.include_router(alerts_router)
router.include_router(detection_router)
router.include_router(admin_router)
router.include_router(health_router)
router.include_router(sse_router)
router.include_router(trajectory_pca_router)
router.include_router(behavioral_baseline_router)
router.include_router(sts_hotspots_router)
router.include_router(jamming_zones_router)
router.include_router(explainability_router)
router.include_router(ownership_network_router)
router.include_router(collaboration_router)
router.include_router(fp_tuning_router)
router.include_router(public_router)
router.include_router(embed_router)
router.include_router(cases_router)
