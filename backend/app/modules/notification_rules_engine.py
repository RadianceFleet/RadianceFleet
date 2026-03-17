"""Notification rules engine — evaluate, match, dispatch, and log alert notifications."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from app.config import settings

logger = logging.getLogger(__name__)


def evaluate_rules(db, alert) -> list:
    """Return all active NotificationRule objects that match the given alert."""
    from app.models.notification_rule import NotificationRule

    if not settings.NOTIFICATION_RULES_ENABLED:
        return []

    rules = (
        db.query(NotificationRule)
        .filter(NotificationRule.is_active == True)  # noqa: E712
        .all()
    )

    vessel = None
    vessel_id = getattr(alert, "vessel_id", None)
    if vessel_id:
        from app.models.vessel import Vessel

        vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()

    matched = []
    for rule in rules:
        if _matches_rule(rule, alert, vessel):
            matched.append(rule)
    return matched


def _matches_rule(rule, alert, vessel) -> bool:
    """Check all conditions on a rule (AND logic). Nullable conditions match any."""
    # Score range
    risk_score = getattr(alert, "risk_score", None)
    if rule.min_score is not None and risk_score is not None and risk_score < rule.min_score:
        return False
    if rule.max_score is not None and risk_score is not None and risk_score > rule.max_score:
        return False

    # Corridor
    if rule.corridor_ids_json:
        corridor_ids = rule.corridor_ids_json if isinstance(rule.corridor_ids_json, list) else []
        alert_corridor = getattr(alert, "corridor_id", None)
        if alert_corridor is None or alert_corridor not in corridor_ids:
            return False

    # Vessel flags
    if rule.vessel_flags_json:
        flags = rule.vessel_flags_json if isinstance(rule.vessel_flags_json, list) else []
        vessel_flag = getattr(vessel, "flag", None) or getattr(vessel, "flag_state", None) if vessel else None
        if vessel_flag is None or vessel_flag not in flags:
            return False

    # Alert status
    if rule.alert_statuses_json:
        statuses = rule.alert_statuses_json if isinstance(rule.alert_statuses_json, list) else []
        alert_status = getattr(alert, "status", None)
        if alert_status is None:
            return False
        status_str = alert_status.value if hasattr(alert_status, "value") else str(alert_status)
        if status_str not in statuses:
            return False

    # Vessel types
    if rule.vessel_types_json:
        types = rule.vessel_types_json if isinstance(rule.vessel_types_json, list) else []
        vessel_type = getattr(vessel, "vessel_type", None) if vessel else None
        if vessel_type is None or vessel_type not in types:
            return False

    # Scoring signals
    if rule.scoring_signals_json:
        signals = rule.scoring_signals_json if isinstance(rule.scoring_signals_json, list) else []
        breakdown = getattr(alert, "risk_breakdown_json", None)
        if breakdown:
            if isinstance(breakdown, str):
                try:
                    breakdown = json.loads(breakdown)
                except (json.JSONDecodeError, TypeError):
                    breakdown = {}
            breakdown_signals = set()
            if isinstance(breakdown, dict):
                for key, val in breakdown.items():
                    if isinstance(val, dict) and val.get("score", 0) > 0 or isinstance(val, (int, float)) and val > 0:
                        breakdown_signals.add(key)
            if not any(s in breakdown_signals for s in signals):
                return False
        else:
            return False

    # Time window
    if rule.time_window_start and rule.time_window_end:
        now = datetime.now(UTC)
        current_time = now.strftime("%H:%M")
        start = rule.time_window_start
        end = rule.time_window_end
        if start <= end:
            if not (start <= current_time <= end):
                return False
        else:
            # Overnight window (e.g. 22:00 - 06:00)
            if not (current_time >= start or current_time <= end):
                return False

    return True


def _is_throttled(db, rule, alert) -> bool:
    """Check if the rule+destination has fired within throttle_minutes."""
    from app.models.notification_rule_log import NotificationRuleLog

    throttle = rule.throttle_minutes or settings.NOTIFICATION_RULES_DEFAULT_THROTTLE_MINUTES
    cutoff = datetime.now(UTC) - timedelta(minutes=throttle)

    recent = (
        db.query(NotificationRuleLog)
        .filter(
            NotificationRuleLog.rule_id == rule.rule_id,
            NotificationRuleLog.destination == rule.destination,
            NotificationRuleLog.status == "sent",
            NotificationRuleLog.sent_at >= cutoff,
        )
        .first()
    )
    return recent is not None


def dispatch_notification(rule, alert, vessel=None) -> dict:
    """Route notification to the appropriate channel. Returns result dict."""
    channel = rule.channel.lower()

    if channel == "slack":
        return _dispatch_slack(rule, alert, vessel)
    elif channel == "email":
        return _dispatch_email(rule, alert, vessel)
    elif channel == "webhook":
        return _dispatch_webhook(rule, alert, vessel)
    else:
        return {"success": False, "error": f"Unknown channel: {channel}"}


def _dispatch_slack(rule, alert, vessel) -> dict:
    """Send alert notification via Slack."""
    from app.modules.slack_notifier import format_alert_for_slack, send_slack_message

    msg = format_alert_for_slack(alert, vessel=vessel, template=rule.message_template)
    result = send_slack_message(rule.destination, msg["text"], msg.get("blocks"))
    return {"success": result.get("ok", False), "error": result.get("error")}


def _dispatch_email(rule, alert, vessel) -> dict:
    """Send alert notification via email."""
    from app.modules.email_notifier import _send_email

    vessel_name = "Unknown"
    if vessel:
        vessel_name = getattr(vessel, "name", None) or getattr(vessel, "vessel_name", None) or "Unknown"

    risk_score = getattr(alert, "risk_score", 0) or 0
    alert_id = getattr(alert, "gap_event_id", None)

    if rule.message_template:
        body = rule.message_template.format(
            vessel_name=vessel_name,
            risk_score=risk_score,
            alert_id=alert_id or "N/A",
            corridor_id=getattr(alert, "corridor_id", None) or "N/A",
            duration_minutes=getattr(alert, "duration_minutes", None) or "N/A",
            status=getattr(alert, "status", "new"),
            flag_state=getattr(vessel, "flag_state", "") if vessel else "",
        )
    else:
        body = (
            f"<p>Alert triggered for vessel <b>{vessel_name}</b>.</p>"
            f"<p>Risk score: <b>{risk_score}</b></p>"
            f"<p>Alert ID: {alert_id}</p>"
            f"<p style='font-size:11px;color:#888'>"
            f"RadianceFleet: investigative triage only, not legal determinations.</p>"
        )

    subject = f"RadianceFleet Alert: {vessel_name} (score {risk_score})"
    ok = _send_email(rule.destination, subject, body)
    return {"success": ok, "error": None if ok else "email_delivery_failed"}


def _dispatch_webhook(rule, alert, vessel) -> dict:
    """Send alert notification via webhook POST."""
    import httpx

    vessel_name = "Unknown"
    if vessel:
        vessel_name = getattr(vessel, "name", None) or getattr(vessel, "vessel_name", None) or "Unknown"

    payload = {
        "event": "alert_notification",
        "timestamp": datetime.now(UTC).isoformat(),
        "alert": {
            "alert_id": getattr(alert, "gap_event_id", None),
            "risk_score": getattr(alert, "risk_score", 0),
            "corridor_id": getattr(alert, "corridor_id", None),
            "status": str(getattr(alert, "status", "new")),
            "duration_minutes": getattr(alert, "duration_minutes", None),
        },
        "vessel": {
            "vessel_name": vessel_name,
            "flag_state": getattr(vessel, "flag", None) or getattr(vessel, "flag_state", None) if vessel else None,
            "vessel_type": getattr(vessel, "vessel_type", None) if vessel else None,
        },
        "rule": {
            "rule_id": rule.rule_id,
            "name": rule.name,
        },
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                rule.destination,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return {"success": True, "error": None}
    except Exception as exc:
        logger.error("Webhook delivery to %s failed: %s", rule.destination, exc)
        return {"success": False, "error": str(exc)}


def fire_matching_rules(db, alert) -> list[dict]:
    """Top-level: evaluate rules, check throttle, dispatch, and log results.

    Returns a list of result dicts for each rule fired.
    """
    from app.models.notification_rule_log import NotificationRuleLog

    if not settings.NOTIFICATION_RULES_ENABLED:
        return []

    # Integration stub for Task 43 alert dedup
    try:
        if hasattr(alert, "alert_group_id") and alert.alert_group_id:
            from app.models.alert_group import AlertGroup

            group = db.query(AlertGroup).get(alert.alert_group_id)
            if group and alert.gap_event_id != group.primary_alert_id:
                return []  # only fire for primary alert in group
    except (ImportError, Exception):  # noqa: S110
        pass  # Task 43 not yet integrated

    matched_rules = evaluate_rules(db, alert)
    results = []

    vessel = None
    vessel_id = getattr(alert, "vessel_id", None)
    if vessel_id:
        from app.models.vessel import Vessel

        vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()

    for rule in matched_rules:
        # Throttle check
        if _is_throttled(db, rule, alert):
            log = NotificationRuleLog(
                rule_id=rule.rule_id,
                alert_id=alert.gap_event_id,
                channel=rule.channel,
                destination=rule.destination,
                status="throttled",
                sent_at=datetime.now(UTC),
            )
            db.add(log)
            db.commit()
            results.append({"rule_id": rule.rule_id, "status": "throttled"})
            continue

        # Dispatch
        result = dispatch_notification(rule, alert, vessel)
        status = "sent" if result.get("success") else "failed"

        log = NotificationRuleLog(
            rule_id=rule.rule_id,
            alert_id=alert.gap_event_id,
            channel=rule.channel,
            destination=rule.destination,
            status=status,
            error_message=result.get("error"),
            sent_at=datetime.now(UTC),
        )
        db.add(log)
        db.commit()
        results.append({"rule_id": rule.rule_id, "status": status, "error": result.get("error")})

    return results
