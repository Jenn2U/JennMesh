"""Tests for v0.6.0 Pydantic models — encryption, webhook, notification, partition, bulk_ops, api."""

from __future__ import annotations

import json

import pytest

from jenn_mesh.models.api import ConfirmRequest, PaginatedResponse, StatusResponse
from jenn_mesh.models.bulk_ops import (
    BulkOperationProgress,
    BulkOperationRequest,
    BulkOperationStatus,
    BulkOperationType,
    TargetFilter,
)
from jenn_mesh.models.encryption import (
    DeviceEncryptionAudit,
    EncryptionStatus,
    FleetEncryptionReport,
)
from jenn_mesh.models.notification import (
    EmailConfig,
    NotificationChannel,
    NotificationChannelType,
    NotificationRule,
    SlackConfig,
    TeamsConfig,
)
from jenn_mesh.models.partition import (
    PartitionEvent,
    PartitionEventType,
    PartitionStatus,
)
from jenn_mesh.models.webhook import WebhookEventType

# ── Encryption Models ────────────────────────────────────────────────


class TestEncryptionModels:
    def test_encryption_status_values(self):
        assert EncryptionStatus.STRONG.value == "strong"
        assert EncryptionStatus.WEAK.value == "weak"
        assert EncryptionStatus.UNENCRYPTED.value == "unencrypted"
        assert EncryptionStatus.UNKNOWN.value == "unknown"

    def test_device_encryption_audit(self):
        audit = DeviceEncryptionAudit(
            node_id="!abc123",
            encryption_status=EncryptionStatus.STRONG,
            channel_count=3,
            strong_channels=["Primary"],
            weak_channels=[],
            uses_default_longfast=False,
        )
        assert audit.node_id == "!abc123"
        assert audit.encryption_status == EncryptionStatus.STRONG

    def test_fleet_encryption_report(self):
        report = FleetEncryptionReport(
            fleet_score=85.5,
            total_devices=10,
            strong_count=8,
            weak_count=1,
            unencrypted_count=1,
            unknown_count=0,
            devices=[],
        )
        assert report.fleet_score == 85.5
        assert report.total_devices == 10


# ── Webhook Models ───────────────────────────────────────────────────


class TestWebhookModels:
    def test_event_types(self):
        assert WebhookEventType.ALERT_CREATED.value == "alert_created"
        assert WebhookEventType.NODE_OFFLINE.value == "node_offline"

    def test_event_type_count(self):
        # Should have ~10 event types
        assert len(WebhookEventType) >= 8


# ── Notification Models ──────────────────────────────────────────────


class TestNotificationModels:
    def test_channel_types(self):
        assert NotificationChannelType.SLACK.value == "slack"
        assert NotificationChannelType.TEAMS.value == "teams"
        assert NotificationChannelType.EMAIL.value == "email"
        assert NotificationChannelType.WEBHOOK.value == "webhook"

    def test_slack_config(self):
        config = SlackConfig(webhook_url="https://hooks.slack.com/x")
        assert config.username == "JennMesh"

    def test_teams_config(self):
        config = TeamsConfig(webhook_url="https://teams.example.com/hook")
        assert config.webhook_url == "https://teams.example.com/hook"

    def test_email_config(self):
        config = EmailConfig(
            smtp_host="smtp.example.com",
            from_address="alerts@example.com",
            to_addresses=["ops@example.com"],
        )
        assert config.smtp_port == 587
        assert config.use_tls is True

    def test_notification_channel(self):
        ch = NotificationChannel(
            name="Ops Slack",
            channel_type=NotificationChannelType.SLACK,
        )
        assert ch.is_active is True
        assert ch.config_json == "{}"

    def test_notification_rule(self):
        rule = NotificationRule(
            name="Critical Alerts",
            alert_types=["low_battery"],
            severities=["critical"],
            channel_ids=[1, 2],
        )
        assert rule.is_active is True
        assert len(rule.channel_ids) == 2


# ── Partition Models ─────────────────────────────────────────────────


class TestPartitionModels:
    def test_event_types(self):
        assert PartitionEventType.PARTITION_DETECTED.value == "partition_detected"
        assert PartitionEventType.PARTITION_RESOLVED.value == "partition_resolved"

    def test_partition_event(self):
        event = PartitionEvent(
            event_type=PartitionEventType.PARTITION_DETECTED,
            component_count=3,
        )
        assert event.previous_component_count == 1
        assert event.relay_recommendation is None

    def test_partition_status(self):
        status = PartitionStatus()
        assert status.is_partitioned is False
        assert status.component_count == 1
        assert status.components == []


# ── Bulk Ops Models ──────────────────────────────────────────────────


class TestBulkOpsModels:
    def test_operation_types(self):
        assert BulkOperationType.CONFIG_PUSH.value == "config_push"
        assert BulkOperationType.REBOOT.value == "reboot"
        assert BulkOperationType.PSK_ROTATION.value == "psk_rotation"

    def test_operation_statuses(self):
        assert BulkOperationStatus.PREVIEW.value == "preview"
        assert BulkOperationStatus.RUNNING.value == "running"
        assert BulkOperationStatus.CANCELLED.value == "cancelled"

    def test_target_filter_defaults(self):
        tf = TargetFilter()
        assert tf.all_devices is False
        assert tf.node_ids is None
        assert tf.role is None

    def test_bulk_operation_request_defaults(self):
        req = BulkOperationRequest(
            operation_type=BulkOperationType.REBOOT,
        )
        assert req.dry_run is True
        assert req.confirmed is False
        assert req.parameters == {}

    def test_bulk_operation_progress(self):
        progress = BulkOperationProgress(
            id=1,
            operation_type="reboot",
            status="running",
            total_targets=5,
            completed_count=3,
        )
        assert progress.failed_count == 0
        assert progress.skipped_count == 0


# ── API Models ───────────────────────────────────────────────────────


class TestAPIModels:
    def test_paginated_response(self):
        resp = PaginatedResponse(count=10, limit=50)
        assert resp.offset == 0

    def test_status_response(self):
        resp = StatusResponse(status="ok", message="All good")
        assert resp.status == "ok"

    def test_confirm_request_defaults(self):
        req = ConfirmRequest()
        assert req.confirmed is False
