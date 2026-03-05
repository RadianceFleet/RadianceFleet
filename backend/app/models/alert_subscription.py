"""Alert subscription model — double opt-in email notifications."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, Boolean, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class AlertSubscription(Base):
    __tablename__ = "alert_subscriptions"
    __table_args__ = (
        UniqueConstraint("email", "mmsi", "corridor_id", name="uq_subscription"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    mmsi: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    corridor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    alert_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    consent_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    consent_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    form_version: Mapped[str] = mapped_column(String(10), nullable=False, default="1.0")


import hmac as _hmac
import hashlib
import time


def generate_subscription_token(email: str, secret: str) -> str:
    """Generate HMAC-SHA256 token with 48h expiry embedded as 'expiry:signature'."""
    expiry = str(int(time.time()) + 48 * 3600)
    sig = _hmac.new(secret.encode(), (email + "|" + expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}:{sig}"


def verify_subscription_token(token: str, email: str, secret: str) -> bool:
    """Verify token is valid and not expired."""
    try:
        expiry_str, sig = token.split(":", 1)
        expiry = int(expiry_str)
        if time.time() > expiry:
            return False
        expected = _hmac.new(secret.encode(), (email + "|" + expiry_str).encode(), hashlib.sha256).hexdigest()
        return _hmac.compare_digest(sig, expected)
    except Exception:
        return False
