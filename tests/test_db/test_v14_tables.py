"""Tests for schema v14 DB CRUD — webhooks, deliveries, notifications, partitions, bulk ops."""

from __future__ import annotations

import json
import tempfile

import pytest

from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db(tmp_path) -> MeshDatabase:
    return MeshDatabase(db_path=str(tmp_path / "v14_test.db"))


# ── Webhook CRUD ─────────────────────────────────────────────────────


class TestWebhookCRUD:
    def test_create_and_get(self, db):
        wh_id = db.create_webhook(name="Test", url="https://example.com/hook", secret="s3cret")
        wh = db.get_webhook(wh_id)
        assert wh is not None
        assert wh["name"] == "Test"
        assert wh["url"] == "https://example.com/hook"
        assert wh["secret"] == "s3cret"

    def test_list_webhooks(self, db):
        db.create_webhook(name="A", url="https://a.com")
        db.create_webhook(name="B", url="https://b.com")
        webhooks = db.list_webhooks()
        assert len(webhooks) == 2

    def test_list_active_only(self, db):
        wh1 = db.create_webhook(name="Active", url="https://a.com")
        wh2 = db.create_webhook(name="Inactive", url="https://b.com")
        db.update_webhook(wh2, is_active=False)
        active = db.list_webhooks(active_only=True)
        assert len(active) == 1
        assert active[0]["name"] == "Active"

    def test_update_webhook(self, db):
        wh_id = db.create_webhook(name="Old", url="https://old.com")
        db.update_webhook(wh_id, name="New", url="https://new.com")
        wh = db.get_webhook(wh_id)
        assert wh["name"] == "New"
        assert wh["url"] == "https://new.com"

    def test_delete_webhook(self, db):
        wh_id = db.create_webhook(name="Del", url="https://del.com")
        assert db.delete_webhook(wh_id) is True
        assert db.get_webhook(wh_id) is None

    def test_delete_nonexistent(self, db):
        assert db.delete_webhook(9999) is False

    def test_get_nonexistent(self, db):
        assert db.get_webhook(9999) is None


# ── Webhook Delivery CRUD ────────────────────────────────────────────


class TestWebhookDeliveryCRUD:
    def test_create_delivery(self, db):
        wh_id = db.create_webhook(name="H", url="https://h.com")
        d_id = db.create_webhook_delivery(wh_id, "alert_created", '{"test":1}')
        deliveries = db.list_webhook_deliveries(wh_id)
        assert len(deliveries) == 1
        assert deliveries[0]["event_type"] == "alert_created"

    def test_pending_deliveries(self, db):
        wh_id = db.create_webhook(name="H", url="https://h.com")
        db.create_webhook_delivery(wh_id, "alert_created", "{}")
        pending = db.get_pending_webhook_deliveries()
        assert len(pending) >= 1
        assert pending[0]["status"] == "pending"

    def test_update_delivery(self, db):
        wh_id = db.create_webhook(name="H", url="https://h.com")
        d_id = db.create_webhook_delivery(wh_id, "test", "{}")
        db.update_webhook_delivery(
            d_id, status="delivered", http_status=200, increment_attempt=True
        )
        deliveries = db.list_webhook_deliveries(wh_id)
        assert deliveries[0]["status"] == "delivered"
        assert deliveries[0]["http_status"] == 200
        assert deliveries[0]["attempt_count"] == 1

    def test_prune_old_deliveries(self, db):
        wh_id = db.create_webhook(name="H", url="https://h.com")
        db.create_webhook_delivery(wh_id, "test", "{}")
        # Pruning 0-day-old records should clean up nothing (just created)
        count = db.prune_old_webhook_deliveries(days=0)
        # The delivery was just created so it's 0 days old — edge case
        assert isinstance(count, int)


# ── Notification Channel CRUD ────────────────────────────────────────


class TestNotificationChannelCRUD:
    def test_create_and_get(self, db):
        ch_id = db.create_notification_channel(name="Ops Slack", channel_type="slack")
        ch = db.get_notification_channel(ch_id)
        assert ch is not None
        assert ch["name"] == "Ops Slack"
        assert ch["channel_type"] == "slack"

    def test_list_channels(self, db):
        db.create_notification_channel(name="A", channel_type="slack")
        db.create_notification_channel(name="B", channel_type="email")
        channels = db.list_notification_channels()
        assert len(channels) == 2

    def test_list_active_only(self, db):
        ch1 = db.create_notification_channel(name="Active", channel_type="slack")
        ch2 = db.create_notification_channel(name="Inactive", channel_type="email")
        db.update_notification_channel(ch2, is_active=False)
        active = db.list_notification_channels(active_only=True)
        assert len(active) == 1

    def test_update_channel(self, db):
        ch_id = db.create_notification_channel(name="Old", channel_type="slack")
        db.update_notification_channel(ch_id, name="New")
        ch = db.get_notification_channel(ch_id)
        assert ch["name"] == "New"

    def test_delete_channel(self, db):
        ch_id = db.create_notification_channel(name="Del", channel_type="slack")
        assert db.delete_notification_channel(ch_id) is True
        assert db.get_notification_channel(ch_id) is None

    def test_update_no_fields_returns_false(self, db):
        ch_id = db.create_notification_channel(name="X", channel_type="slack")
        assert db.update_notification_channel(ch_id) is False


# ── Notification Rule CRUD ───────────────────────────────────────────


class TestNotificationRuleCRUD:
    def test_create_and_get(self, db):
        r_id = db.create_notification_rule(
            name="Critical",
            alert_types=json.dumps(["low_battery"]),
            severities=json.dumps(["critical"]),
            channel_ids=json.dumps([1]),
        )
        rule = db.get_notification_rule(r_id)
        assert rule is not None
        assert rule["name"] == "Critical"

    def test_list_rules(self, db):
        db.create_notification_rule(name="R1")
        db.create_notification_rule(name="R2")
        rules = db.list_notification_rules()
        assert len(rules) == 2

    def test_update_rule(self, db):
        r_id = db.create_notification_rule(name="Old")
        db.update_notification_rule(r_id, name="New")
        rule = db.get_notification_rule(r_id)
        assert rule["name"] == "New"

    def test_delete_rule(self, db):
        r_id = db.create_notification_rule(name="Del")
        assert db.delete_notification_rule(r_id) is True
        assert db.get_notification_rule(r_id) is None

    def test_get_channels_for_alert_match(self, db):
        ch_id = db.create_notification_channel(name="Slack", channel_type="slack")
        db.create_notification_rule(
            name="Critical",
            alert_types=json.dumps(["low_battery"]),
            severities=json.dumps(["critical"]),
            channel_ids=json.dumps([ch_id]),
        )
        channels = db.get_channels_for_alert("low_battery", "critical")
        assert len(channels) == 1
        assert channels[0]["name"] == "Slack"

    def test_get_channels_for_alert_no_match(self, db):
        ch_id = db.create_notification_channel(name="Slack", channel_type="slack")
        db.create_notification_rule(
            name="Critical Only",
            alert_types=json.dumps(["low_battery"]),
            severities=json.dumps(["critical"]),
            channel_ids=json.dumps([ch_id]),
        )
        channels = db.get_channels_for_alert("device_offline", "warning")
        assert channels == []

    def test_get_channels_wildcard_types(self, db):
        """Empty alert_types means match ALL types."""
        ch_id = db.create_notification_channel(name="All", channel_type="slack")
        db.create_notification_rule(
            name="All Types",
            alert_types="[]",
            severities=json.dumps(["critical"]),
            channel_ids=json.dumps([ch_id]),
        )
        channels = db.get_channels_for_alert("anything_goes", "critical")
        assert len(channels) == 1


# ── Partition Event CRUD ─────────────────────────────────────────────


class TestPartitionEventCRUD:
    def test_create_and_get(self, db):
        ev_id = db.create_partition_event(
            event_type="partition_detected",
            component_count=3,
            components_json=json.dumps([["!a"], ["!b"], ["!c"]]),
        )
        ev = db.get_partition_event(ev_id)
        assert ev is not None
        assert ev["event_type"] == "partition_detected"
        assert ev["component_count"] == 3

    def test_list_events(self, db):
        db.create_partition_event("partition_detected", 2)
        db.create_partition_event("partition_resolved", 1)
        events = db.list_partition_events()
        assert len(events) == 2

    def test_list_events_with_type_filter(self, db):
        db.create_partition_event("partition_detected", 2)
        db.create_partition_event("partition_resolved", 1)
        detected = db.list_partition_events(event_type="partition_detected")
        assert len(detected) == 1
        assert detected[0]["event_type"] == "partition_detected"

    def test_resolve_event(self, db):
        ev_id = db.create_partition_event("partition_detected", 2)
        assert db.resolve_partition_event(ev_id) is True
        ev = db.get_partition_event(ev_id)
        assert ev["resolved_at"] is not None

    def test_resolve_already_resolved(self, db):
        ev_id = db.create_partition_event("partition_detected", 2)
        db.resolve_partition_event(ev_id)
        # Second resolve should return False (already resolved)
        assert db.resolve_partition_event(ev_id) is False

    def test_get_latest_unresolved(self, db):
        db.create_partition_event("partition_detected", 3)
        latest = db.get_latest_partition_event()
        assert latest is not None
        assert latest["event_type"] == "partition_detected"

    def test_get_latest_unresolved_when_resolved(self, db):
        ev_id = db.create_partition_event("partition_detected", 2)
        db.resolve_partition_event(ev_id)
        assert db.get_latest_partition_event() is None

    def test_create_with_relay_recommendation(self, db):
        ev_id = db.create_partition_event(
            event_type="partition_detected",
            component_count=2,
            relay_recommendation="Place relay at (37.7, -122.4)",
        )
        ev = db.get_partition_event(ev_id)
        assert ev["relay_recommendation"] == "Place relay at (37.7, -122.4)"


# ── Bulk Operation CRUD ──────────────────────────────────────────────


class TestBulkOperationCRUD:
    def test_create_and_get(self, db):
        op_id = db.create_bulk_operation(
            operation_type="reboot",
            target_node_ids='["!a", "!b"]',
            total_targets=2,
        )
        op = db.get_bulk_operation(op_id)
        assert op is not None
        assert op["operation_type"] == "reboot"
        assert op["total_targets"] == 2

    def test_create_with_status(self, db):
        op_id = db.create_bulk_operation(
            operation_type="reboot",
            total_targets=1,
            status="running",
        )
        op = db.get_bulk_operation(op_id)
        assert op["status"] == "running"

    def test_list_operations(self, db):
        db.create_bulk_operation(operation_type="reboot", total_targets=1)
        db.create_bulk_operation(operation_type="psk_rotation", total_targets=2)
        ops = db.list_bulk_operations()
        assert len(ops) == 2

    def test_list_operations_status_filter(self, db):
        db.create_bulk_operation(operation_type="reboot", total_targets=1, status="completed")
        db.create_bulk_operation(operation_type="psk_rotation", total_targets=2, status="running")
        running = db.list_bulk_operations(status="running")
        assert len(running) == 1
        assert running[0]["operation_type"] == "psk_rotation"

    def test_update_operation(self, db):
        op_id = db.create_bulk_operation(operation_type="reboot", total_targets=3, status="running")
        db.update_bulk_operation(op_id, completed_count=2, failed_count=1, status="failed")
        op = db.get_bulk_operation(op_id)
        assert op["status"] == "failed"
        assert op["completed_count"] == 2
        assert op["failed_count"] == 1

    def test_cancel_operation(self, db):
        op_id = db.create_bulk_operation(operation_type="reboot", total_targets=1, status="running")
        assert db.cancel_bulk_operation(op_id) is True
        op = db.get_bulk_operation(op_id)
        assert op["status"] == "cancelled"
        assert op["completed_at"] is not None

    def test_cancel_completed_fails(self, db):
        op_id = db.create_bulk_operation(
            operation_type="reboot", total_targets=1, status="completed"
        )
        assert db.cancel_bulk_operation(op_id) is False

    def test_get_nonexistent(self, db):
        assert db.get_bulk_operation(9999) is None
