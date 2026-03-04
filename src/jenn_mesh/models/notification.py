"""Notification channel and rule models for multi-channel alert routing."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NotificationChannelType(str, Enum):
    """Supported notification channel types."""

    SLACK = "slack"
    TEAMS = "teams"
    EMAIL = "email"
    WEBHOOK = "webhook"


class SlackConfig(BaseModel):
    """Slack channel configuration."""

    webhook_url: str = Field(description="Slack Incoming Webhook URL")
    channel: str = Field(default="", description="Optional channel override")
    username: str = Field(default="JennMesh", description="Bot username")


class TeamsConfig(BaseModel):
    """Microsoft Teams channel configuration."""

    webhook_url: str = Field(description="Teams Incoming Webhook URL")


class EmailConfig(BaseModel):
    """Email channel configuration."""

    smtp_host: str = Field(description="SMTP server hostname")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_user: str = Field(default="", description="SMTP username")
    smtp_pass: str = Field(default="", description="SMTP password")
    from_address: str = Field(description="Sender email address")
    to_addresses: list[str] = Field(description="Recipient email addresses")
    use_tls: bool = Field(default=True, description="Use STARTTLS")


class NotificationChannel(BaseModel):
    """A notification delivery channel."""

    id: Optional[int] = Field(default=None, description="Channel ID (auto-assigned)")
    name: str = Field(description="Human-readable channel name")
    channel_type: NotificationChannelType
    config_json: str = Field(default="{}", description="Channel-specific config (JSON)")
    is_active: bool = Field(default=True)


class NotificationRule(BaseModel):
    """Alert routing rule — maps alert conditions to channels."""

    id: Optional[int] = Field(default=None, description="Rule ID (auto-assigned)")
    name: str = Field(description="Human-readable rule name")
    alert_types: list[str] = Field(
        default_factory=list,
        description="Alert types to match (empty = all types)",
    )
    severities: list[str] = Field(
        default_factory=list,
        description="Severity levels to match (empty = all severities)",
    )
    channel_ids: list[int] = Field(
        default_factory=list,
        description="Target channel IDs for matching alerts",
    )
    is_active: bool = Field(default=True)
