"""Tests for OTA Config Rollback (MESH-040)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from jenn_mesh.agent.remote_admin import RemoteAdminResult
from jenn_mesh.core.config_rollback import ConfigRollbackManager
from jenn_mesh.db import SCHEMA_VERSION, MeshDatabase

SAMPLE_YAML = """\
owner:
  long_name: Node-A
  short_name: NA
lora:
  region: US
  hop_limit: 5
"""

SAMPLE_YAML_NEW = """\
owner:
  long_name: Node-A
  short_name: NA
lora:
  region: US
  hop_limit: 3
"""


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db() -> MeshDatabase:
    """Fresh test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def manager(db: MeshDatabase) -> ConfigRollbackManager:
    """Rollback manager with mocked admin."""
    return ConfigRollbackManager(db=db, monitoring_minutes=10)


def _seed_device(db: MeshDatabase, node_id: str = "!a", status: str = "reachable") -> None:
    """Seed a single device."""
    db.upsert_device(node_id, long_name="Node-A", role="CLIENT")
    with db.connection() as conn:
        conn.execute(
            "UPDATE devices SET last_seen = datetime('now'),"
            f" mesh_status = '{status}' WHERE node_id = ?",
            (node_id,),
        )


# ── DB methods for config_snapshots ──────────────────────────────────


class TestConfigSnapshotDB:
    """Verify schema v10 config_snapshots DB methods."""

    def test_create_snapshot(self, db: MeshDatabase) -> None:
        snap_id = db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)
        assert isinstance(snap_id, int)
        assert snap_id > 0

    def test_create_snapshot_no_yaml(self, db: MeshDatabase) -> None:
        snap_id = db.create_config_snapshot("!a", "drift_remediation")
        snap = db.get_config_snapshot(snap_id)
        assert snap is not None
        assert snap["yaml_before"] is None
        assert snap["status"] == "active"

    def test_update_snapshot(self, db: MeshDatabase) -> None:
        snap_id = db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)
        db.update_config_snapshot(snap_id, status="monitoring", yaml_after=SAMPLE_YAML_NEW)
        snap = db.get_config_snapshot(snap_id)
        assert snap["status"] == "monitoring"
        assert snap["yaml_after"] == SAMPLE_YAML_NEW

    def test_update_snapshot_noop(self, db: MeshDatabase) -> None:
        snap_id = db.create_config_snapshot("!a", "bulk_push")
        db.update_config_snapshot(snap_id)  # No kwargs — no-op
        snap = db.get_config_snapshot(snap_id)
        assert snap["status"] == "active"

    def test_get_snapshot_not_found(self, db: MeshDatabase) -> None:
        assert db.get_config_snapshot(99999) is None

    def test_get_snapshots_for_node(self, db: MeshDatabase) -> None:
        db.create_config_snapshot("!a", "bulk_push", yaml_before="y1")
        db.create_config_snapshot("!a", "drift_remediation", yaml_before="y2")
        db.create_config_snapshot("!b", "bulk_push", yaml_before="y3")
        snaps = db.get_snapshots_for_node("!a")
        assert len(snaps) == 2
        assert all(s["node_id"] == "!a" for s in snaps)

    def test_get_snapshots_for_node_limit(self, db: MeshDatabase) -> None:
        for i in range(5):
            db.create_config_snapshot("!a", "bulk_push", yaml_before=f"y{i}")
        snaps = db.get_snapshots_for_node("!a", limit=3)
        assert len(snaps) == 3

    def test_get_monitoring_snapshots(self, db: MeshDatabase) -> None:
        s1 = db.create_config_snapshot("!a", "bulk_push", yaml_before="y1")
        s2 = db.create_config_snapshot("!b", "bulk_push", yaml_before="y2")
        fmt = "%Y-%m-%d %H:%M:%S"
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(fmt)
        db.update_config_snapshot(s1, status="monitoring", monitoring_until=future)
        db.update_config_snapshot(s2, status="confirmed")

        monitoring = db.get_monitoring_snapshots()
        assert len(monitoring) == 1
        assert monitoring[0]["id"] == s1

    def test_get_monitoring_snapshots_includes_expired(self, db: MeshDatabase) -> None:
        """Expired monitoring windows are still returned — caller decides action."""
        s1 = db.create_config_snapshot("!a", "bulk_push", yaml_before="y1")
        fmt = "%Y-%m-%d %H:%M:%S"
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(fmt)
        db.update_config_snapshot(s1, status="monitoring", monitoring_until=past)
        monitoring = db.get_monitoring_snapshots()
        assert len(monitoring) == 1  # Returned — _should_rollback decides next step

    def test_get_recent_snapshots(self, db: MeshDatabase) -> None:
        for i in range(3):
            db.create_config_snapshot(f"!{i}", "bulk_push")
        recent = db.get_recent_snapshots(limit=10)
        assert len(recent) == 3

    def test_schema_version_current(self, db: MeshDatabase) -> None:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
        assert row["version"] == SCHEMA_VERSION


# ── snapshot_before_push ─────────────────────────────────────────────


class TestSnapshotBeforePush:
    def test_snapshot_success(self, manager: ConfigRollbackManager) -> None:
        mock_result = RemoteAdminResult(
            success=True, node_id="!a", command="export-config", output=SAMPLE_YAML
        )
        with patch.object(manager._admin, "get_remote_config", return_value=mock_result):
            snap_id = manager.snapshot_before_push("!a", "bulk_push")

        assert snap_id is not None
        snap = manager.db.get_config_snapshot(snap_id)
        assert snap["yaml_before"] == SAMPLE_YAML
        assert snap["status"] == "active"

    def test_snapshot_failure_still_returns_id(self, manager: ConfigRollbackManager) -> None:
        mock_result = RemoteAdminResult(
            success=False, node_id="!a", command="export-config", error="timeout"
        )
        with patch.object(manager._admin, "get_remote_config", return_value=mock_result):
            snap_id = manager.snapshot_before_push("!a", "bulk_push")

        assert snap_id is not None
        snap = manager.db.get_config_snapshot(snap_id)
        assert snap["status"] == "snapshot_failed"
        assert "timeout" in snap["error"]

    def test_snapshot_exception_still_returns_id(self, manager: ConfigRollbackManager) -> None:
        with patch.object(manager._admin, "get_remote_config", side_effect=RuntimeError("boom")):
            snap_id = manager.snapshot_before_push("!a", "bulk_push")

        assert snap_id is not None
        snap = manager.db.get_config_snapshot(snap_id)
        assert snap["status"] == "snapshot_failed"
        assert "boom" in snap["error"]

    def test_snapshot_reuse_recent(self, manager: ConfigRollbackManager) -> None:
        """If a recent snapshot exists, reuse its yaml_before."""
        # First snapshot — fetches from device
        mock_result = RemoteAdminResult(
            success=True, node_id="!a", command="export-config", output=SAMPLE_YAML
        )
        with patch.object(manager._admin, "get_remote_config", return_value=mock_result) as mock:
            snap1_id = manager.snapshot_before_push("!a", "bulk_push")
            assert mock.call_count == 1

        # Second snapshot — should reuse (no remote call)
        with patch.object(manager._admin, "get_remote_config") as mock2:
            snap2_id = manager.snapshot_before_push("!a", "bulk_push")
            mock2.assert_not_called()

        snap2 = manager.db.get_config_snapshot(snap2_id)
        assert snap2["yaml_before"] == SAMPLE_YAML
        assert snap2["id"] != snap1_id  # New record, reused content


# ── mark_push_completed / mark_push_failed ───────────────────────────


class TestMarkPush:
    def test_mark_push_completed(self, manager: ConfigRollbackManager) -> None:
        snap_id = manager.db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)
        manager.mark_push_completed(snap_id, SAMPLE_YAML_NEW)

        snap = manager.db.get_config_snapshot(snap_id)
        assert snap["status"] == "monitoring"
        assert snap["yaml_after"] == SAMPLE_YAML_NEW
        assert snap["push_completed_at"] is not None
        assert snap["monitoring_until"] is not None

    def test_mark_push_failed(self, manager: ConfigRollbackManager) -> None:
        snap_id = manager.db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)
        manager.mark_push_failed(snap_id, "Radio unreachable")

        snap = manager.db.get_config_snapshot(snap_id)
        assert snap["status"] == "push_failed"
        assert snap["error"] == "Radio unreachable"


# ── auto_rollback ────────────────────────────────────────────────────


class TestAutoRollback:
    def test_rollback_success(self, manager: ConfigRollbackManager, db: MeshDatabase) -> None:
        _seed_device(db, "!a", "unreachable")
        snap_id = db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)

        mock_result = RemoteAdminResult(
            success=True, node_id="!a", command="configure", output="ok"
        )
        with patch.object(manager._admin, "apply_remote_config", return_value=mock_result):
            result = manager.auto_rollback(snap_id)

        assert result["success"] is True
        snap = db.get_config_snapshot(snap_id)
        assert snap["status"] == "rolled_back"
        assert snap["rolled_back_at"] is not None

    def test_rollback_creates_alerts(
        self, manager: ConfigRollbackManager, db: MeshDatabase
    ) -> None:
        _seed_device(db, "!a")
        snap_id = db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)

        mock_result = RemoteAdminResult(
            success=True, node_id="!a", command="configure", output="ok"
        )
        with patch.object(manager._admin, "apply_remote_config", return_value=mock_result):
            manager.auto_rollback(snap_id)

        # Should have both TRIGGERED and COMPLETED alerts
        alerts = db.get_active_alerts("!a")
        alert_types = [a["alert_type"] for a in alerts]
        assert "config_rollback_triggered" in alert_types
        assert "config_rollback_completed" in alert_types

    def test_rollback_failure(self, manager: ConfigRollbackManager, db: MeshDatabase) -> None:
        _seed_device(db, "!a")
        snap_id = db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)

        mock_result = RemoteAdminResult(
            success=False, node_id="!a", command="configure", error="mesh error"
        )
        with patch.object(manager._admin, "apply_remote_config", return_value=mock_result):
            result = manager.auto_rollback(snap_id)

        assert result["success"] is False
        snap = db.get_config_snapshot(snap_id)
        assert snap["status"] == "rollback_failed"

    def test_rollback_no_yaml_before(
        self, manager: ConfigRollbackManager, db: MeshDatabase
    ) -> None:
        _seed_device(db, "!a")
        snap_id = db.create_config_snapshot("!a", "bulk_push")  # No yaml_before

        result = manager.auto_rollback(snap_id)
        assert result["success"] is False
        assert "No yaml_before" in result["error"]

    def test_rollback_snapshot_not_found(self, manager: ConfigRollbackManager) -> None:
        result = manager.auto_rollback(99999)
        assert "error" in result

    def test_rollback_exception(self, manager: ConfigRollbackManager, db: MeshDatabase) -> None:
        _seed_device(db, "!a")
        snap_id = db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)

        with patch.object(
            manager._admin, "apply_remote_config", side_effect=RuntimeError("kaboom")
        ):
            result = manager.auto_rollback(snap_id)

        assert result["success"] is False
        snap = db.get_config_snapshot(snap_id)
        assert snap["status"] == "rollback_failed"


# ── manual_rollback ──────────────────────────────────────────────────


class TestManualRollback:
    def test_manual_rollback(self, manager: ConfigRollbackManager, db: MeshDatabase) -> None:
        _seed_device(db, "!a")
        snap_id = db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)

        mock_result = RemoteAdminResult(
            success=True, node_id="!a", command="configure", output="ok"
        )
        with patch.object(manager._admin, "apply_remote_config", return_value=mock_result):
            result = manager.manual_rollback(snap_id)

        assert result["success"] is True

    def test_manual_rollback_no_yaml(
        self, manager: ConfigRollbackManager, db: MeshDatabase
    ) -> None:
        snap_id = db.create_config_snapshot("!a", "bulk_push")
        result = manager.manual_rollback(snap_id)
        assert "error" in result

    def test_manual_rollback_not_found(self, manager: ConfigRollbackManager) -> None:
        result = manager.manual_rollback(99999)
        assert "error" in result


# ── _should_rollback (monitoring decision logic) ─────────────────────


class TestShouldRollback:
    def _make_snapshot(self, monitoring_until: str | None = None) -> dict:
        """Create a minimal snapshot dict for testing."""
        return {
            "id": 1,
            "node_id": "!a",
            "push_source": "bulk_push",
            "yaml_before": SAMPLE_YAML,
            "monitoring_until": monitoring_until,
        }

    def test_wait_during_monitoring_window(self, manager: ConfigRollbackManager) -> None:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        snap = self._make_snapshot(monitoring_until=future)
        assert manager._should_rollback(snap, {"mesh_status": "unreachable"}) == "wait"

    def test_confirm_when_online_after_window(self, manager: ConfigRollbackManager) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        snap = self._make_snapshot(monitoring_until=past)
        device = {"mesh_status": "reachable"}
        assert manager._should_rollback(snap, device) == "confirm"

    def test_rollback_when_offline_after_window(self, manager: ConfigRollbackManager) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        snap = self._make_snapshot(monitoring_until=past)
        device = {"mesh_status": "unreachable"}
        # First call returns "wait" (hysteresis: needs 2 consecutive failures)
        assert manager._should_rollback(snap, device) == "wait"
        # Second call reaches threshold → "rollback"
        assert manager._should_rollback(snap, device) == "rollback"

    def test_confirm_when_device_gone(self, manager: ConfigRollbackManager) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        snap = self._make_snapshot(monitoring_until=past)
        assert manager._should_rollback(snap, None) == "confirm"

    def test_confirm_when_no_yaml_before(self, manager: ConfigRollbackManager) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        snap = self._make_snapshot(monitoring_until=past)
        snap["yaml_before"] = None
        device = {"mesh_status": "unreachable"}
        assert manager._should_rollback(snap, device) == "confirm"

    def test_no_monitoring_until_evaluates_immediately(
        self, manager: ConfigRollbackManager
    ) -> None:
        snap = self._make_snapshot(monitoring_until=None)
        device = {"mesh_status": "reachable"}
        assert manager._should_rollback(snap, device) == "confirm"


# ── check_post_push_failures ─────────────────────────────────────────


class TestCheckPostPushFailures:
    _FMT = "%Y-%m-%d %H:%M:%S"

    def test_confirms_healthy_node(self, manager: ConfigRollbackManager, db: MeshDatabase) -> None:
        _seed_device(db, "!a", "reachable")
        snap_id = db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime(self._FMT)
        db.update_config_snapshot(snap_id, status="monitoring", monitoring_until=past)

        result = manager.check_post_push_failures()
        assert result["confirmed"] == 1
        assert result["rolled_back"] == 0

        snap = db.get_config_snapshot(snap_id)
        assert snap["status"] == "confirmed"

    def test_rollback_offline_node(self, manager: ConfigRollbackManager, db: MeshDatabase) -> None:
        _seed_device(db, "!a", "unreachable")
        snap_id = db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime(self._FMT)
        db.update_config_snapshot(snap_id, status="monitoring", monitoring_until=past)

        mock_result = RemoteAdminResult(
            success=True, node_id="!a", command="configure", output="ok"
        )
        with patch.object(manager._admin, "apply_remote_config", return_value=mock_result):
            # First call: hysteresis — offline check 1/2, returns "wait"
            result = manager.check_post_push_failures()
            assert result["rolled_back"] == 0

            # Second call: reaches threshold → triggers rollback
            result = manager.check_post_push_failures()

        assert result["rolled_back"] == 1
        snap = db.get_config_snapshot(snap_id)
        assert snap["status"] == "rolled_back"

    def test_waits_during_window(self, manager: ConfigRollbackManager, db: MeshDatabase) -> None:
        _seed_device(db, "!a", "unreachable")
        snap_id = db.create_config_snapshot("!a", "bulk_push", yaml_before=SAMPLE_YAML)
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(self._FMT)
        db.update_config_snapshot(snap_id, status="monitoring", monitoring_until=future)

        result = manager.check_post_push_failures()
        assert result["confirmed"] == 0
        assert result["rolled_back"] == 0

        snap = db.get_config_snapshot(snap_id)
        assert snap["status"] == "monitoring"  # Unchanged

    def test_empty_monitoring_list(self, manager: ConfigRollbackManager) -> None:
        result = manager.check_post_push_failures()
        assert result["monitoring_count"] == 0


# ── Query methods ────────────────────────────────────────────────────


class TestQueryMethods:
    def test_get_snapshot(self, manager: ConfigRollbackManager) -> None:
        snap_id = manager.db.create_config_snapshot("!a", "bulk_push", yaml_before="y1")
        snap = manager.get_snapshot(snap_id)
        assert snap is not None
        assert snap["node_id"] == "!a"

    def test_get_node_history(self, manager: ConfigRollbackManager) -> None:
        manager.db.create_config_snapshot("!a", "bulk_push")
        manager.db.create_config_snapshot("!a", "drift_remediation")
        history = manager.get_node_history("!a")
        assert len(history) == 2

    def test_get_rollback_status(self, manager: ConfigRollbackManager) -> None:
        manager.db.create_config_snapshot("!a", "bulk_push")
        status = manager.get_rollback_status()
        assert "monitoring_count" in status
        assert "status_breakdown" in status
        assert "monitoring_minutes" in status
        assert status["monitoring_minutes"] == 10
