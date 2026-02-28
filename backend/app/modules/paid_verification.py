"""Pay-per-verification architecture for commercial data sources.

Strategy: Use paid sources ONLY when our free pipeline flags a vessel
at score >= 76 (critical). This keeps costs at $1K-5K/yr instead of
$50K+ subscription fees.

Providers:
- Skylight (Allen AI): Free for qualifying NGOs — satellite-AIS correlation
- Spire Maritime: Satellite AIS position verification
- S&P Sea-web: Beneficial ownership + P&I insurance lookup
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import settings
from app.models.verification_log import VerificationLog
from app.models.vessel import Vessel

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Result from a paid verification query."""
    provider: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    error: str | None = None


class VerificationProvider(ABC):
    """Abstract base class for paid verification providers."""

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def verify_vessel(self, vessel: Vessel) -> VerificationResult:
        ...

    @abstractmethod
    def estimated_cost(self) -> float:
        """Estimated cost per query in USD."""
        ...


class SkylightProvider(VerificationProvider):
    """Skylight (Allen AI) — satellite-AIS correlation.

    Free for qualifying NGOs. Provides 290K vessel detections/week.
    """

    def name(self) -> str:
        return "skylight"

    def estimated_cost(self) -> float:
        return 0.0  # Free for NGOs

    def verify_vessel(self, vessel: Vessel) -> VerificationResult:
        api_key = getattr(settings, 'SKYLIGHT_API_KEY', None)
        if not api_key:
            return VerificationResult(
                provider=self.name(), success=False,
                error="SKYLIGHT_API_KEY not configured. Apply at https://skylight.global/",
            )

        # Stub: actual implementation requires Skylight API documentation
        logger.info("Skylight verification for vessel %s (stub)", vessel.mmsi)
        return VerificationResult(
            provider=self.name(), success=False,
            error="Skylight API integration pending — apply for NGO access first",
        )


class SpireProvider(VerificationProvider):
    """Spire Maritime — satellite AIS position verification."""

    def name(self) -> str:
        return "spire"

    def estimated_cost(self) -> float:
        return 0.50  # Estimated per-query cost

    def verify_vessel(self, vessel: Vessel) -> VerificationResult:
        api_key = getattr(settings, 'SPIRE_API_KEY', None)
        if not api_key:
            return VerificationResult(
                provider=self.name(), success=False,
                error="SPIRE_API_KEY not configured",
            )

        # Stub: actual Spire API integration
        logger.info("Spire verification for vessel %s (stub)", vessel.mmsi)
        return VerificationResult(
            provider=self.name(), success=False,
            error="Spire API integration pending — requires paid API access",
            cost_usd=self.estimated_cost(),
        )


class SeaWebProvider(VerificationProvider):
    """S&P Global Sea-web — ownership + P&I insurance lookup."""

    def name(self) -> str:
        return "seaweb"

    def estimated_cost(self) -> float:
        return 2.00  # Estimated per-query cost

    def verify_vessel(self, vessel: Vessel) -> VerificationResult:
        api_key = getattr(settings, 'SEAWEB_API_KEY', None)
        if not api_key:
            return VerificationResult(
                provider=self.name(), success=False,
                error="SEAWEB_API_KEY not configured",
            )

        # Stub: actual Sea-web API integration
        logger.info("Sea-web verification for vessel %s (stub)", vessel.mmsi)
        return VerificationResult(
            provider=self.name(), success=False,
            error="Sea-web API integration pending — requires paid subscription",
            cost_usd=self.estimated_cost(),
        )


_PROVIDERS: dict[str, VerificationProvider] = {
    "skylight": SkylightProvider(),
    "spire": SpireProvider(),
    "seaweb": SeaWebProvider(),
}


def get_monthly_spend(db: Session) -> float:
    """Get total USD spent on paid verifications this calendar month."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = db.query(func.sum(VerificationLog.cost_usd)).filter(
        VerificationLog.request_time_utc >= month_start,
        VerificationLog.response_status == "success",
    ).scalar()
    return float(result or 0.0)


def verify_vessel(
    db: Session,
    vessel_id: int,
    provider_name: str = "skylight",
) -> VerificationResult:
    """Run paid verification for a vessel.

    Enforces budget limits before making external API calls.
    Logs all attempts to verification_logs table.
    """
    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        return VerificationResult(provider=provider_name, success=False, error="Vessel not found")

    provider = _PROVIDERS.get(provider_name)
    if not provider:
        return VerificationResult(
            provider=provider_name, success=False,
            error=f"Unknown provider: {provider_name}. Available: {list(_PROVIDERS.keys())}",
        )

    # Budget check
    budget = getattr(settings, 'VERIFICATION_MONTHLY_BUDGET_USD', 500.0)
    current_spend = get_monthly_spend(db)
    if current_spend + provider.estimated_cost() > budget:
        log = VerificationLog(
            vessel_id=vessel_id, provider=provider_name,
            response_status="budget_exceeded",
            cost_usd=0.0,
            result_summary=f"Monthly budget ${budget:.2f} exceeded (current: ${current_spend:.2f})",
        )
        db.add(log)
        db.commit()
        return VerificationResult(
            provider=provider_name, success=False,
            error=f"Monthly budget exceeded: ${current_spend:.2f} / ${budget:.2f}",
        )

    # Execute verification
    result = provider.verify_vessel(vessel)

    # Log result
    log = VerificationLog(
        vessel_id=vessel_id,
        provider=provider_name,
        response_status="success" if result.success else "error",
        cost_usd=result.cost_usd,
        result_summary=str(result.data)[:500] if result.success else result.error,
    )
    db.add(log)
    db.commit()

    return result


def get_budget_status(db: Session) -> dict:
    """Get current verification budget status."""
    budget = getattr(settings, 'VERIFICATION_MONTHLY_BUDGET_USD', 500.0)
    spent = get_monthly_spend(db)
    return {
        "monthly_budget_usd": budget,
        "spent_usd": round(spent, 2),
        "remaining_usd": round(max(0, budget - spent), 2),
        "providers": list(_PROVIDERS.keys()),
    }
