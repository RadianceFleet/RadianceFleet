"""Slack notification delivery via Slack Web API (chat.postMessage)."""

from __future__ import annotations

import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_SLACK_POST_URL = "https://slack.com/api/chat.postMessage"


def send_slack_message(
    channel_id: str, text: str, blocks: list[dict] | None = None
) -> dict:
    """Post a message to a Slack channel using the Bot Token.

    Returns the Slack API response dict.  Raises on HTTP errors.
    """
    if not settings.SLACK_BOT_TOKEN:
        logger.warning("SLACK_BOT_TOKEN not configured — message not sent")
        return {"ok": False, "error": "not_configured"}

    payload: dict = {"channel": channel_id, "text": text}
    if blocks:
        payload["blocks"] = json.dumps(blocks) if isinstance(blocks, list) else blocks

    headers = {
        "Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(_SLACK_POST_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error("Slack API error: %s", data.get("error", "unknown"))
            return data
    except Exception as exc:
        logger.error("Slack delivery failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def format_alert_for_slack(alert, vessel=None, template: str | None = None) -> dict:
    """Build a Block Kit message for an alert notification.

    Returns ``{"text": ..., "blocks": [...]}``.
    """
    vessel_name = "Unknown"
    flag_state = ""
    if vessel:
        vessel_name = getattr(vessel, "name", None) or getattr(vessel, "vessel_name", None) or "Unknown"
        flag_state = getattr(vessel, "flag", None) or getattr(vessel, "flag_state", "") or ""

    risk_score = getattr(alert, "risk_score", 0) or 0
    corridor_id = getattr(alert, "corridor_id", None)
    duration = getattr(alert, "duration_minutes", None)
    status = getattr(alert, "status", "new")
    alert_id = getattr(alert, "gap_event_id", None)

    if template:
        text = template.format(
            vessel_name=vessel_name,
            flag_state=flag_state,
            risk_score=risk_score,
            corridor_id=corridor_id or "N/A",
            duration_minutes=duration or "N/A",
            status=status,
            alert_id=alert_id or "N/A",
        )
        return {"text": text, "blocks": None}

    # Default Block Kit layout
    score_emoji = ":red_circle:" if risk_score >= 70 else ":large_orange_circle:" if risk_score >= 40 else ":white_circle:"
    duration_str = f"{duration} min" if duration else "N/A"

    text = f"Alert: {vessel_name} (score {risk_score})"
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Maritime Alert: {vessel_name}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Risk Score:* {score_emoji} {risk_score}"},
                {"type": "mrkdwn", "text": f"*Flag:* {flag_state or 'Unknown'}"},
                {"type": "mrkdwn", "text": f"*Corridor:* {corridor_id or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Gap Duration:* {duration_str}"},
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Alert ID: {alert_id} | Status: {status}",
                }
            ],
        },
    ]
    return {"text": text, "blocks": blocks}
