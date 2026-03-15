"""Export delivery — email, S3, and webhook delivery methods for bulk exports."""

from __future__ import annotations

import hashlib
import hmac
import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger(__name__)

# 10 MB attachment limit
MAX_EMAIL_ATTACHMENT_BYTES = 10 * 1024 * 1024


def deliver_via_email(file_bytes: bytes, filename: str, config: dict) -> dict:
    """Deliver export file via email attachment.

    Config keys: email (recipient address).
    Enforces 10 MB attachment limit.
    """
    to_email = config.get("email")
    if not to_email:
        return {"status": "failed", "error": "No email address in delivery config"}

    if len(file_bytes) > MAX_EMAIL_ATTACHMENT_BYTES:
        return {
            "status": "failed",
            "error": f"File too large for email ({len(file_bytes)} bytes > 10 MB limit)",
        }

    subject = f"RadianceFleet Export: {filename}"
    html_body = f"""
    <p>Your scheduled RadianceFleet export is ready.</p>
    <p>File: <b>{filename}</b> ({len(file_bytes):,} bytes)</p>
    <p style="font-size:11px;color:#888">
      RadianceFleet — open source maritime anomaly detection.<br>
      Outputs are investigative triage, not legal determinations.
    </p>
    """

    if settings.RESEND_API_KEY:
        return _send_via_resend(to_email, subject, html_body, file_bytes, filename)
    elif settings.SMTP_HOST:
        return _send_via_smtp(to_email, subject, html_body, file_bytes, filename)
    else:
        return {"status": "failed", "error": "No email provider configured"}


def _send_via_resend(
    to_email: str, subject: str, html_body: str, file_bytes: bytes, filename: str
) -> dict:
    import resend

    resend.api_key = settings.RESEND_API_KEY
    try:
        resend.Emails.send(
            {
                "from": f"RadianceFleet <noreply@{settings.EMAIL_FROM_DOMAIN}>",
                "to": [to_email],
                "subject": subject,
                "html": html_body,
                "attachments": [{"filename": filename, "content": list(file_bytes)}],
            }
        )
        return {"status": "sent", "method": "resend"}
    except Exception as e:
        logger.error("Resend API error for export delivery: %s", e)
        return {"status": "failed", "error": str(e)}


def _send_via_smtp(
    to_email: str, subject: str, html_body: str, file_bytes: bytes, filename: str
) -> dict:
    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = f"RadianceFleet <{settings.SMTP_USER or 'noreply@radiancefleet.com'}>"
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        attachment = MIMEApplication(file_bytes, Name=filename)
        attachment["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(attachment)

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASS:
                server.login(settings.SMTP_USER, settings.SMTP_PASS)
            server.sendmail(
                settings.SMTP_USER or "noreply@radiancefleet.com",
                to_email,
                msg.as_string(),
            )
        return {"status": "sent", "method": "smtp"}
    except Exception as e:
        logger.error("SMTP error for export delivery: %s", e)
        return {"status": "failed", "error": str(e)}


def deliver_via_s3(file_bytes: bytes, filename: str, config: dict) -> dict:
    """Deliver export file to S3 bucket.

    Config keys: bucket, prefix, region, endpoint_url.
    """
    try:
        import boto3
    except ImportError:
        return {"status": "failed", "error": "boto3 not installed"}

    bucket = config.get("bucket", settings.EXPORT_S3_BUCKET)
    prefix = config.get("prefix", settings.EXPORT_S3_PREFIX) or ""
    region = config.get("region", settings.EXPORT_S3_REGION)
    endpoint_url = config.get("endpoint_url", settings.EXPORT_S3_ENDPOINT_URL)

    if not bucket:
        return {"status": "failed", "error": "No S3 bucket configured"}

    key = f"{prefix.rstrip('/')}/{filename}" if prefix else filename

    try:
        kwargs = {}
        if region:
            kwargs["region_name"] = region
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url

        s3 = boto3.client("s3", **kwargs)
        import io

        s3.upload_fileobj(io.BytesIO(file_bytes), bucket, key)
        return {"status": "sent", "method": "s3", "bucket": bucket, "key": key}
    except Exception as e:
        logger.error("S3 upload error: %s", e)
        return {"status": "failed", "error": str(e)}


def deliver_via_webhook(file_bytes: bytes, filename: str, config: dict) -> dict:
    """Deliver export file via webhook (multipart upload with HMAC signature).

    Config keys: url, secret (optional).
    """
    import httpx

    url = config.get("url")
    if not url:
        return {"status": "failed", "error": "No webhook URL in delivery config"}

    secret = config.get("secret")

    headers = {}
    if secret:
        sig = hmac.new(secret.encode(), file_bytes, hashlib.sha256).hexdigest()
        headers["X-Webhook-Signature"] = sig

    try:
        files = {"file": (filename, file_bytes)}
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, files=files, headers=headers)
            resp.raise_for_status()
        return {"status": "sent", "method": "webhook", "status_code": resp.status_code}
    except Exception as e:
        logger.error("Webhook delivery error: %s", e)
        return {"status": "failed", "error": str(e)}


def deliver(file_bytes: bytes, filename: str, method: str, config: dict) -> dict:
    """Dispatch delivery to the appropriate method."""
    dispatchers = {
        "email": deliver_via_email,
        "s3": deliver_via_s3,
        "webhook": deliver_via_webhook,
    }
    dispatcher = dispatchers.get(method)
    if not dispatcher:
        return {"status": "failed", "error": f"Unknown delivery method: {method}"}
    return dispatcher(file_bytes, filename, config)
