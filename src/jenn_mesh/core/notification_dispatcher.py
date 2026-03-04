"""Notification Dispatcher — multi-channel alert routing and delivery.

Routes alerts to matching notification channels based on configured rules.
Formats payloads per channel type (Slack Block Kit, Teams Adaptive Card,
plain text email) and delegates delivery to the webhook engine (Slack/Teams)
or SMTP (email).

Usage::

    dispatcher = NotificationDispatcher(db=db, webhook_manager=wh_manager)
    count = dispatcher.notify("low_battery", "warning", {"node_id": "!abc", ...})
"""

from __future__ import annotations

import json
import logging
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any, Optional

from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)


def _format_slack(
    alert_type: str,
    severity: str,
    data: dict[str, Any],
) -> dict:
    """Format an alert as a Slack Block Kit payload.

    Uses color-coded severity indicators:
    - critical → 🔴
    - warning  → 🟡
    - info     → 🔵

    Args:
        alert_type: The alert type string (e.g. "low_battery").
        severity:   Severity level ("critical", "warning", "info").
        data:       Event-specific data dict.

    Returns:
        Slack Block Kit payload dict ready for JSON serialization.
    """
    severity_emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "⚪")
    node_id = data.get("node_id", "unknown")
    message = data.get("message", alert_type.replace("_", " ").title())

    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity_emoji} JennMesh Alert: {alert_type}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Severity:* {severity}"},
                    {"type": "mrkdwn", "text": f"*Node:* `{node_id}`"},
                    {"type": "mrkdwn", "text": f"*Message:* {message}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Time:* {datetime.now(timezone.utc).strftime('%H:%M UTC')}",
                    },
                ],
            },
        ],
    }


def _format_teams(
    alert_type: str,
    severity: str,
    data: dict[str, Any],
) -> dict:
    """Format an alert as a Microsoft Teams Adaptive Card payload."""
    color_map = {"critical": "attention", "warning": "warning", "info": "accent"}
    color = color_map.get(severity, "default")
    node_id = data.get("node_id", "unknown")
    message = data.get("message", alert_type.replace("_", " ").title())

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": f"JennMesh Alert: {alert_type}",
                            "weight": "Bolder",
                            "size": "Medium",
                            "color": color,
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "Severity", "value": severity},
                                {"title": "Node", "value": node_id},
                                {"title": "Message", "value": message},
                            ],
                        },
                    ],
                },
            }
        ],
    }


def _format_email(
    alert_type: str,
    severity: str,
    data: dict[str, Any],
) -> str:
    """Format an alert as a plain-text email body."""
    node_id = data.get("node_id", "unknown")
    message = data.get("message", alert_type.replace("_", " ").title())
    timestamp = datetime.now(timezone.utc).isoformat()

    return (
        f"JennMesh Fleet Alert\n"
        f"{'=' * 40}\n\n"
        f"Type:     {alert_type}\n"
        f"Severity: {severity}\n"
        f"Node:     {node_id}\n"
        f"Message:  {message}\n"
        f"Time:     {timestamp}\n\n"
        f"— JennMesh Dashboard\n"
    )


class NotificationDispatcher:
    """Routes alerts to matching notification channels."""

    def __init__(
        self,
        db: MeshDatabase,
        webhook_manager: Optional[object] = None,
    ) -> None:
        self.db = db
        self.webhook_manager = webhook_manager

    def notify(
        self,
        alert_type: str,
        severity: str,
        data: dict[str, Any],
    ) -> int:
        """Send an alert to all matching notification channels.

        Looks up active rules that match the alert_type and severity,
        resolves their channel_ids, formats the payload per channel type,
        and delivers.

        Returns the number of channels notified.
        """
        channels = self.db.get_channels_for_alert(alert_type, severity)
        if not channels:
            return 0

        sent = 0
        for ch in channels:
            ch_type = ch.get("channel_type", "")
            config = json.loads(ch.get("config_json", "{}"))
            try:
                if ch_type == "slack":
                    self._send_slack(config, alert_type, severity, data)
                    sent += 1
                elif ch_type == "teams":
                    self._send_teams(config, alert_type, severity, data)
                    sent += 1
                elif ch_type == "email":
                    self._send_email(config, alert_type, severity, data)
                    sent += 1
                elif ch_type == "webhook":
                    self._send_webhook(config, alert_type, severity, data)
                    sent += 1
                else:
                    logger.warning("Unknown channel type '%s' for channel %d", ch_type, ch["id"])
            except Exception:
                logger.exception(
                    "Failed to deliver notification to channel %d (%s)",
                    ch["id"],
                    ch.get("name", ""),
                )

        if sent:
            logger.info("Notified %d channel(s) for %s/%s", sent, alert_type, severity)
        return sent

    def _send_slack(
        self,
        config: dict,
        alert_type: str,
        severity: str,
        data: dict[str, Any],
    ) -> None:
        """Deliver Slack Block Kit message via Incoming Webhook."""
        import httpx

        url = config.get("webhook_url", "")
        if not url:
            logger.warning("Slack channel missing webhook_url")
            return
        payload = _format_slack(alert_type, severity, data)
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()

    def _send_teams(
        self,
        config: dict,
        alert_type: str,
        severity: str,
        data: dict[str, Any],
    ) -> None:
        """Deliver Teams Adaptive Card via Incoming Webhook."""
        import httpx

        url = config.get("webhook_url", "")
        if not url:
            logger.warning("Teams channel missing webhook_url")
            return
        payload = _format_teams(alert_type, severity, data)
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()

    def _send_email(
        self,
        config: dict,
        alert_type: str,
        severity: str,
        data: dict[str, Any],
    ) -> None:
        """Send plain-text email via SMTP."""
        body = _format_email(alert_type, severity, data)
        msg = MIMEText(body)
        msg["Subject"] = f"[JennMesh] {severity.upper()}: {alert_type}"
        msg["From"] = config.get("from_address", "noreply@jenn2u.ai")
        to_addrs = config.get("to_addresses", [])
        msg["To"] = ", ".join(to_addrs)

        host = config.get("smtp_host", "localhost")
        port = config.get("smtp_port", 587)
        use_tls = config.get("use_tls", True)

        with smtplib.SMTP(host, port) as server:
            if use_tls:
                server.starttls()
            user = config.get("smtp_user", "")
            passwd = config.get("smtp_pass", "")
            if user:
                server.login(user, passwd)
            server.sendmail(msg["From"], to_addrs, msg.as_string())

    def _send_webhook(
        self,
        config: dict,
        alert_type: str,
        severity: str,
        data: dict[str, Any],
    ) -> None:
        """Deliver via generic webhook (delegates to WebhookManager dispatch)."""
        if self.webhook_manager is not None:
            # Use the webhook manager's dispatch for proper HMAC + retry
            self.webhook_manager.dispatch_event(  # type: ignore[attr-defined]
                f"notification.{alert_type}", data
            )
        else:
            # Fallback: direct HTTP POST
            import httpx

            url = config.get("url", "")
            if url:
                payload = {
                    "alert_type": alert_type,
                    "severity": severity,
                    "data": data,
                    "source": "jenn-mesh",
                }
                with httpx.Client(timeout=10.0) as client:
                    client.post(url, json=payload)
