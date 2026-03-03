"""Tests for ConfigQueueManager — enqueue, retry, backoff, escalation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from jenn_mesh.agent.remote_admin import RemoteAdminResult
from jenn_mesh.core.config_queue_manager import ConfigQueueManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.config_queue import (
    ConfigQueueStatus,
    compute_next_retry_delay,
)


@pytest.fixture()
def db(tmp_path):
    """Fresh in-memory-like DB for each test."""
    db_path = str(tmp_path / "test.db")
    return MeshDatabase(db_path=db_path)


@pytest.fixture()
def manager(db):
    """ConfigQueueManager with a test DB."""
    return ConfigQueueManager(db=db, admin_port="test")


@pytest.fixture()
def sample_yaml():
    return "owner:\n  long_name: TestRelay\n"


@pytest.fixture()
def sample_hash():
    return "abc123def456"


# ── Enqueue tests ──────────────────────────────────────────────────────


class TestEnqueue:
    def test_creates_entry(self, manager, sample_yaml, sample_hash):
        entry = manager.enqueue(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        assert entry.id is not None
        assert entry.target_node_id == "!aaa11111"
        assert entry.template_role == "relay-node"
        assert entry.config_hash == sample_hash
        assert entry.yaml_content == sample_yaml
        assert entry.status == ConfigQueueStatus.PENDING
        assert entry.retry_count == 0

    def test_sets_next_retry_at(self, manager, sample_yaml, sample_hash):
        entry = manager.enqueue(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        # next_retry_at should be set (defaulted to now by DB)
        assert entry.next_retry_at is not None

    def test_with_source_push_id(self, manager, sample_yaml, sample_hash):
        entry = manager.enqueue(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
            source_push_id="push-001",
        )
        assert entry.source_push_id == "push-001"

    def test_custom_max_retries(self, manager, sample_yaml, sample_hash):
        entry = manager.enqueue(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
            max_retries=5,
        )
        assert entry.max_retries == 5


# ── Process pending tests ──────────────────────────────────────────────


class TestProcessPending:
    def test_empty_queue(self, manager):
        result = manager.process_pending()
        assert result == {
            "attempted": 0,
            "delivered": 0,
            "failed": 0,
            "escalated": 0,
        }

    def test_not_due_entries_skipped(self, manager, db, sample_yaml, sample_hash):
        """Entries with future next_retry_at are not processed."""
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        # Set next_retry_at far in the future
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        db.update_config_queue_status(entry_id, "pending", next_retry_at=future)
        result = manager.process_pending()
        assert result["attempted"] == 0

    @patch("jenn_mesh.core.config_queue_manager.RemoteAdmin")
    def test_successful_delivery(self, MockAdmin, manager, db, sample_yaml, sample_hash):
        """Mock RemoteAdmin success → entry marked delivered."""
        mock_admin = MockAdmin.return_value
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=True,
            node_id="!aaa11111",
            command="configure",
            output="OK",
        )
        # Create entry with past next_retry_at
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        db.update_config_queue_status(entry_id, "pending", next_retry_at=past)

        result = manager.process_pending()
        assert result["attempted"] == 1
        assert result["delivered"] == 1

        entry = db.get_config_queue_entry(entry_id)
        assert entry["status"] == "delivered"
        assert entry["delivered_at"] is not None

    @patch("jenn_mesh.core.config_queue_manager.RemoteAdmin")
    def test_failed_delivery_increments_retry(
        self, MockAdmin, manager, db, sample_yaml, sample_hash
    ):
        """Mock failure → retry_count incremented, next_retry_at updated."""
        mock_admin = MockAdmin.return_value
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=False,
            node_id="!aaa11111",
            command="configure",
            error="Connection timeout",
        )
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        db.update_config_queue_status(entry_id, "pending", next_retry_at=past)

        result = manager.process_pending()
        assert result["attempted"] == 1
        assert result["failed"] == 1

        entry = db.get_config_queue_entry(entry_id)
        assert entry["retry_count"] == 1
        assert entry["last_error"] == "Connection timeout"
        assert entry["status"] == "pending"

    @patch("jenn_mesh.core.config_queue_manager.RemoteAdmin")
    def test_backoff_schedule(self, MockAdmin, manager, db, sample_yaml, sample_hash):
        """Retry delay follows exponential backoff."""
        mock_admin = MockAdmin.return_value
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=False,
            node_id="!aaa11111",
            command="configure",
            error="timeout",
        )
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        # Set retry_count=2 so next will be 3 → delay=480s
        db.update_config_queue_status(entry_id, "pending", retry_count=2)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        db.update_config_queue_status(entry_id, "pending", next_retry_at=past)

        before = datetime.now(timezone.utc)
        manager.process_pending()
        entry = db.get_config_queue_entry(entry_id)

        assert entry["retry_count"] == 3
        expected_delay = compute_next_retry_delay(3)  # 480s = 8 min
        next_retry = datetime.fromisoformat(entry["next_retry_at"])
        # Next retry should be roughly expected_delay seconds from now
        diff = (next_retry - before).total_seconds()
        assert expected_delay - 5 <= diff <= expected_delay + 5

    @patch("jenn_mesh.core.config_queue_manager.RemoteAdmin")
    def test_max_retries_escalates(self, MockAdmin, manager, db, sample_yaml, sample_hash):
        """After max_retries, status = failed_permanent, alert created."""
        mock_admin = MockAdmin.return_value
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=False,
            node_id="!aaa11111",
            command="configure",
            error="persistent failure",
        )
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
            max_retries=3,
        )
        # Set retry_count=2, so next failure (3rd attempt) hits max
        db.update_config_queue_status(entry_id, "pending", retry_count=2)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        db.update_config_queue_status(entry_id, "pending", next_retry_at=past)

        result = manager.process_pending()
        assert result["escalated"] == 1

        entry = db.get_config_queue_entry(entry_id)
        assert entry["status"] == "failed_permanent"
        assert entry["escalated_at"] is not None

    @patch("jenn_mesh.core.config_queue_manager.RemoteAdmin")
    def test_escalation_creates_alert(self, MockAdmin, manager, db, sample_yaml, sample_hash):
        """Alert has type CONFIG_PUSH_FAILED, correct node_id."""
        mock_admin = MockAdmin.return_value
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=False,
            node_id="!aaa11111",
            command="configure",
            error="radio offline",
        )
        # Register the device first (for FK)
        db.upsert_device("!aaa11111", long_name="TestNode")

        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
            max_retries=1,
        )
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        db.update_config_queue_status(entry_id, "pending", next_retry_at=past)

        manager.process_pending()

        # Check alert was created
        alerts = db.get_active_alerts(node_id="!aaa11111")
        config_alerts = [a for a in alerts if a["alert_type"] == "config_push_failed"]
        assert len(config_alerts) == 1
        assert "relay-node" in config_alerts[0]["message"]
        assert config_alerts[0]["severity"] == "warning"


# ── Manual retry tests ──────────────────────────────────────────────


class TestManualRetry:
    def test_resets_to_pending(self, manager, db, sample_yaml, sample_hash):
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        db.update_config_queue_status(entry_id, "failed_permanent", retry_count=10)

        result = manager.manual_retry(entry_id)
        assert result is not None
        assert result["status"] == "pending"
        assert result["next_retry_at"] is not None

    def test_preserves_retry_count(self, manager, db, sample_yaml, sample_hash):
        """retry_count is NOT reset — preserves audit trail."""
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        db.update_config_queue_status(entry_id, "failed_permanent", retry_count=10)

        result = manager.manual_retry(entry_id)
        assert result["retry_count"] == 10

    def test_invalid_id_returns_none(self, manager):
        assert manager.manual_retry(99999) is None

    def test_cannot_retry_delivered(self, manager, db, sample_yaml, sample_hash):
        """Cannot retry a delivered entry."""
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        db.update_config_queue_status(entry_id, "delivered")
        assert manager.manual_retry(entry_id) is None

    def test_retry_cancelled_entry(self, manager, db, sample_yaml, sample_hash):
        """Can retry a cancelled entry."""
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        db.update_config_queue_status(entry_id, "cancelled")

        result = manager.manual_retry(entry_id)
        assert result is not None
        assert result["status"] == "pending"


# ── Cancel tests ────────────────────────────────────────────────────


class TestCancelEntry:
    def test_cancel_pending(self, manager, db, sample_yaml, sample_hash):
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        assert manager.cancel_entry(entry_id) is True
        entry = db.get_config_queue_entry(entry_id)
        assert entry["status"] == "cancelled"

    def test_cancel_invalid_id(self, manager):
        assert manager.cancel_entry(99999) is False

    def test_cancel_delivered_fails(self, manager, db, sample_yaml, sample_hash):
        """Cannot cancel a delivered entry."""
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        db.update_config_queue_status(entry_id, "delivered")
        assert manager.cancel_entry(entry_id) is False


# ── List/get tests ──────────────────────────────────────────────────


class TestListAndGet:
    def test_get_entry(self, manager, db, sample_yaml, sample_hash):
        entry_id = db.create_config_queue_entry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash=sample_hash,
            yaml_content=sample_yaml,
        )
        entry = manager.get_entry(entry_id)
        assert entry is not None
        assert entry["id"] == entry_id
        assert entry["target_node_id"] == "!aaa11111"

    def test_get_entry_not_found(self, manager):
        assert manager.get_entry(99999) is None

    def test_list_all(self, manager, db, sample_yaml, sample_hash):
        db.create_config_queue_entry("!aaa11111", "relay", sample_hash, sample_yaml)
        db.create_config_queue_entry("!bbb22222", "client", sample_hash, sample_yaml)
        entries = manager.list_entries()
        assert len(entries) == 2

    def test_list_filtered_by_node(self, manager, db, sample_yaml, sample_hash):
        db.create_config_queue_entry("!aaa11111", "relay", sample_hash, sample_yaml)
        db.create_config_queue_entry("!bbb22222", "client", sample_hash, sample_yaml)
        entries = manager.list_entries(target_node_id="!aaa11111")
        assert len(entries) == 1
        assert entries[0]["target_node_id"] == "!aaa11111"

    def test_list_filtered_by_status(self, manager, db, sample_yaml, sample_hash):
        id1 = db.create_config_queue_entry("!aaa11111", "relay", sample_hash, sample_yaml)
        db.create_config_queue_entry("!bbb22222", "client", sample_hash, sample_yaml)
        db.update_config_queue_status(id1, "delivered")
        entries = manager.list_entries(status="delivered")
        assert len(entries) == 1


# ── Summary/status tests ────────────────────────────────────────────


class TestSummaryAndStatus:
    def test_queue_summary(self, manager, db, sample_yaml, sample_hash):
        id1 = db.create_config_queue_entry("!aaa11111", "relay", sample_hash, sample_yaml)
        db.create_config_queue_entry("!bbb22222", "relay", sample_hash, sample_yaml)
        db.update_config_queue_status(id1, "delivered")
        summary = manager.get_queue_summary()
        assert summary.get("delivered", 0) == 1
        assert summary.get("pending", 0) == 1

    def test_device_queue_status(self, manager, db, sample_yaml, sample_hash):
        db.create_config_queue_entry("!aaa11111", "relay", sample_hash, sample_yaml)
        db.create_config_queue_entry("!aaa11111", "client", sample_hash, sample_yaml)
        status = manager.get_device_queue_status("!aaa11111")
        assert status["node_id"] == "!aaa11111"
        assert status["total_entries"] == 2
        assert status["pending_count"] == 2
