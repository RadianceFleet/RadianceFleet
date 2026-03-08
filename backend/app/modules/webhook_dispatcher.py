"""Webhook dispatcher — delivers event payloads to registered webhook URLs."""
import hashlib
import hmac
import json
import logging
import httpx
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _sign_payload(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def dispatch_webhook(url: str, event_type: str, data: dict, secret: str | None = None):
    """Send webhook with retry (3 attempts, exponential backoff)."""
    payload = json.dumps({"event": event_type, "data": data, "timestamp": datetime.now(timezone.utc).isoformat()}).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Webhook-Signature"] = _sign_payload(payload, secret)

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, content=payload, headers=headers)
                resp.raise_for_status()
                return True
        except Exception as e:
            logger.warning("Webhook delivery attempt %d failed for %s: %s", attempt + 1, url, e)
            if attempt < 2:
                import asyncio
                await asyncio.sleep(2 ** attempt)
    return False


async def fire_webhooks(db_session, event_type: str, data: dict):
    """Fire all active webhooks matching the event type."""
    from app.models.webhook import Webhook
    webhooks = db_session.query(Webhook).filter(
        Webhook.is_active == True  # noqa: E712
    ).all()

    for wh in webhooks:
        events = [e.strip() for e in (wh.events or "").split(",")]
        if event_type in events or "all" in events:
            await dispatch_webhook(wh.url, event_type, data, wh.secret)
