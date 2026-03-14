"""Tip submission model — public crowdsourced anomaly reports."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TipSubmission(Base):
    __tablename__ = "tip_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mmsi: Mapped[str] = mapped_column(String(20), nullable=False)
    imo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    behavior_type: Mapped[str] = mapped_column(String(30), nullable=False)
    detail_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    submitter_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    submitter_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    analyst_note: Mapped[str | None] = mapped_column(Text, nullable=True)


ALLOWED_SOURCE_DOMAINS = {
    "marinetraffic.com",
    "vesselfinder.com",
    "globalfishingwatch.org",
    "fleetmon.com",
    "myshiptracking.com",
}


def validate_source_url(url: str | None) -> str | None:
    """Return url if domain is in allowlist, raise ValueError if not."""
    if not url:
        return None
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().removeprefix("www.")
        if not any(domain == d or domain.endswith("." + d) for d in ALLOWED_SOURCE_DOMAINS):
            raise ValueError(
                f"Source URL domain not in allowlist. Allowed: {sorted(ALLOWED_SOURCE_DOMAINS)}"
            )
        return url
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Invalid source URL") from exc
