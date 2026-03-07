"""Email notification via Resend API with SMTP fallback."""
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import settings

logger = logging.getLogger(__name__)


def send_confirmation_email(to_email: str, confirm_url: str) -> bool:
    """Send double opt-in confirmation email. Returns True on success."""
    subject = "Confirm your RadianceFleet alert subscription"
    html_body = f"""
    <p>Click to confirm your RadianceFleet alert subscription:</p>
    <p><a href="{confirm_url}">{confirm_url}</a></p>
    <p>This link expires in 48 hours.</p>
    <p style="font-size:11px;color:#888">
      RadianceFleet — open source maritime anomaly detection.<br>
      Outputs are investigative triage, not legal determinations.
    </p>
    """
    return _send_email(to_email, subject, html_body)


def send_alert_notification(
    to_email: str,
    vessel_name: str,
    alert_type: str,
    alert_url: str,
    unsubscribe_url: str,
) -> bool:
    """Send alert notification email. Returns True on success."""
    subject = f"RadianceFleet: {alert_type} — {vessel_name}"
    html_body = f"""
    <p>A new anomaly was detected for vessel <b>{vessel_name}</b>.</p>
    <p>Alert type: <b>{alert_type}</b></p>
    <p><a href="{alert_url}">View details →</a></p>
    <p style="font-size:11px;color:#888">
      <a href="{unsubscribe_url}">Unsubscribe</a> — RFC 8058 List-Unsubscribe compliant.<br>
      RadianceFleet: investigative triage only, not legal determinations.
    </p>
    """
    return _send_email(to_email, subject, html_body)


def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Try Resend API first, fall back to SMTP."""
    if settings.RESEND_API_KEY:
        return _send_via_resend(to_email, subject, html_body)
    elif settings.SMTP_HOST:
        return _send_via_smtp(to_email, subject, html_body)
    else:
        logger.warning("No email provider configured. Email not sent to %s", to_email)
        return False


def _send_via_resend(to_email: str, subject: str, html_body: str) -> bool:
    import resend
    resend.api_key = settings.RESEND_API_KEY
    try:
        resend.Emails.send({
            "from": f"RadianceFleet <noreply@{settings.EMAIL_FROM_DOMAIN}>",
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        })
        return True
    except Exception as e:
        logger.error("Resend API error: %s", e)
        return False


def _send_via_smtp(to_email: str, subject: str, html_body: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"RadianceFleet <{settings.SMTP_USER or 'noreply@radiancefleet.com'}>"
        msg["To"] = to_email
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASS:
                server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.sendmail(
                settings.SMTP_USER or "noreply@radiancefleet.com",
                to_email,
                msg.as_string(),
            )
        return True
    except Exception as e:
        logger.error("SMTP error: %s", e)
        return False
