"""Tests for FailoverManager — automated failover assess, execute, revert."""

from __future__ import annotations

import json
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.agent.remote_admin import RemoteAdminResult
from jenn_mesh.core.failover_manager import FailoverManager
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db() -> MeshDatabase:
    """Fresh test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


def _seed_linear_topology(db: MeshDatabase) -> None:
    """Seed A-B-C linear chain: B is SPOF, removing B isolates C."""
    db.upsert_device("!a", long_name="Node-A", battery_level=80)
    db.upsert_device("!b", long_name="Relay-B", battery_level=90)
    db.upsert_device("!c", long_name="Node-C", battery_level=70)
    db.upsert_topology_edge("!a", "!b", snr=10.0)
    db.upsert_topology_edge("!b", "!c", snr=10.0)


def _seed_star_topology(db: MeshDatabase) -> None:
    """Seed star: hub connects A, B, C, D. Hub is SPOF.
    Also A↔D cross-link so they stay connected when hub removed.
    """
    db.upsert_device("!hub", long_name="Hub", battery_level=90)
    db.upsert_device("!a", long_name="Node-A", battery_level=80)
    db.upsert_device("!b", long_name="Node-B", battery_level=60)
    db.upsert_device("!c", long_name="Node-C", battery_level=50)
    db.upsert_device("!d", long_name="Node-D", battery_level=20)  # low battery
    db.upsert_topology_edge("!hub", "!a", snr=10.0)
    db.upsert_topology_edge("!hub", "!b", snr=10.0)
    db.upsert_topology_edge("!hub", "!c", snr=10.0)
    db.upsert_topology_edge("!hub", "!d", snr=10.0)
    db.upsert_topology_edge("!a", "!d", snr=5.0)  # cross-link


# ── Impact assessment ────────────────────────────────────────────────


class TestAssessFailoverImpact:
    """Tests for assess_failover_impact()."""

    def test_assess_spof_node(self, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        manager = FailoverManager(db)
        result = manager.assess_failover_impact("!b")

        assert result["failed_node_id"] == "!b"
        assert result["is_spof"] is True
        assert len(result["dependent_nodes"]) >= 1
        assert isinstance(result["compensation_candidates"], list)

    def test_assess_non_spof_node(self, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        manager = FailoverManager(db)
        result = manager.assess_failover_impact("!a")

        assert result["is_spof"] is False
        assert result["dependent_nodes"] == []

    def test_assess_device_not_found(self, db: MeshDatabase) -> None:
        manager = FailoverManager(db)
        with pytest.raises(ValueError, match="not found"):
            manager.assess_failover_impact("!unknown")

    def test_assess_with_candidates(self, db: MeshDatabase) -> None:
        _seed_star_topology(db)
        manager = FailoverManager(db)
        result = manager.assess_failover_impact("!hub")

        assert result["is_spof"] is True
        # Some nodes should be candidates
        candidate_ids = {c["node_id"] for c in result["compensation_candidates"]}
        # !d has low battery (20%) — should be excluded
        assert "!d" not in candidate_ids


# ── Execute failover ─────────────────────────────────────────────────


class TestExecuteFailover:
    """Tests for execute_failover()."""

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_successful_execution(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        result = manager.execute_failover("!b")

        assert result["failed_node_id"] == "!b"
        assert result["status"] == "active"
        assert result["event_id"] is not None
        assert result["applied"] >= 0

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_creates_failover_event(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        result = manager.execute_failover("!b")

        event = db.get_failover_event(result["event_id"])
        assert event is not None
        assert event["failed_node_id"] == "!b"
        assert event["status"] == "active"

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_creates_alert(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        manager.execute_failover("!b")

        assert db.has_active_alert("!b", "failover_activated") is True

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_logs_provisioning(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        manager.execute_failover("!b", operator="test-user")

        log = db.get_provisioning_log_for_node("!b")
        assert len(log) >= 1
        assert log[0]["action"] == "failover_execute"
        assert log[0]["operator"] == "test-user"

    def test_device_not_found(self, db: MeshDatabase) -> None:
        manager = FailoverManager(db)
        with pytest.raises(ValueError, match="not found"):
            manager.execute_failover("!unknown")

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_duplicate_failover_rejected(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        manager.execute_failover("!b")

        with pytest.raises(ValueError, match="already exists"):
            manager.execute_failover("!b")

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_partial_failure(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        """Some compensations succeed, some fail."""
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        call_count = [0]

        def alternating_result(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] % 2 == 0:
                return RemoteAdminResult(
                    success=False, node_id="!a", command="set", output="", error="timeout"
                )
            return RemoteAdminResult(success=True, node_id="!a", command="set", output="OK")

        mock_admin.set_remote_config.side_effect = alternating_result
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        result = manager.execute_failover("!b")

        assert result["applied"] + result["failed"] == result["total_compensations"]


# ── Revert failover ──────────────────────────────────────────────────


class TestRevertFailover:
    """Tests for revert_failover()."""

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_successful_revert(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        exec_result = manager.execute_failover("!b")
        revert_result = manager.revert_failover(exec_result["event_id"])

        assert revert_result["status"] == "reverted"
        assert revert_result["reverted"] >= 0

        # Event should be reverted
        event = db.get_failover_event(exec_result["event_id"])
        assert event["status"] == "reverted"

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_revert_resolves_activated_alert(
        self, mock_admin_cls: MagicMock, db: MeshDatabase
    ) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        exec_result = manager.execute_failover("!b")
        assert db.has_active_alert("!b", "failover_activated") is True

        manager.revert_failover(exec_result["event_id"])
        assert db.has_active_alert("!b", "failover_activated") is False
        assert db.has_active_alert("!b", "failover_reverted") is True

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_revert_failure_creates_critical_alert(
        self, mock_admin_cls: MagicMock, db: MeshDatabase
    ) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        # Execute succeeds
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        exec_result = manager.execute_failover("!b")

        # Now make revert fail
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=False, node_id="!a", command="set", output="", error="offline"
        )

        revert_result = manager.revert_failover(exec_result["event_id"])
        assert revert_result["status"] == "revert_failed"
        assert db.has_active_alert("!b", "failover_revert_failed") is True

    def test_revert_event_not_found(self, db: MeshDatabase) -> None:
        manager = FailoverManager(db)
        with pytest.raises(ValueError, match="not found"):
            manager.revert_failover(9999)

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_revert_already_reverted(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        exec_result = manager.execute_failover("!b")
        manager.revert_failover(exec_result["event_id"])

        with pytest.raises(ValueError, match="not 'active'"):
            manager.revert_failover(exec_result["event_id"])


# ── Cancel failover ──────────────────────────────────────────────────


class TestCancelFailover:
    """Tests for cancel_failover()."""

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_cancel_active_event(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        exec_result = manager.execute_failover("!b")
        cancel_result = manager.cancel_failover(exec_result["event_id"])

        assert cancel_result["status"] == "cancelled"
        event = db.get_failover_event(exec_result["event_id"])
        assert event["status"] == "cancelled"

    def test_cancel_not_found(self, db: MeshDatabase) -> None:
        manager = FailoverManager(db)
        with pytest.raises(ValueError, match="not found"):
            manager.cancel_failover(9999)


# ── Failover status ──────────────────────────────────────────────────


class TestGetFailoverStatus:
    """Tests for get_failover_status()."""

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_status_with_active_failover(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        manager.execute_failover("!b")

        status = manager.get_failover_status("!b")
        assert status["has_active_failover"] is True
        assert status["active_event"] is not None
        assert len(status["active_alerts"]) >= 1

    def test_status_no_failover(self, db: MeshDatabase) -> None:
        db.upsert_device("!a")
        manager = FailoverManager(db)
        status = manager.get_failover_status("!a")
        assert status["has_active_failover"] is False
        assert status["active_event"] is None

    def test_status_device_not_found(self, db: MeshDatabase) -> None:
        manager = FailoverManager(db)
        with pytest.raises(ValueError, match="not found"):
            manager.get_failover_status("!unknown")


# ── List active failovers ────────────────────────────────────────────


class TestListActiveFailovers:
    """Tests for list_active_failovers()."""

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_lists_active_events(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        manager.execute_failover("!b")

        active = manager.list_active_failovers()
        assert len(active) == 1
        assert active[0]["failed_node_id"] == "!b"
        assert "compensations" in active[0]

    def test_empty_when_no_failovers(self, db: MeshDatabase) -> None:
        manager = FailoverManager(db)
        assert manager.list_active_failovers() == []


# ── Check recoveries ────────────────────────────────────────────────


class TestCheckRecoveries:
    """Tests for check_recoveries()."""

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_auto_revert_on_recovery(self, mock_admin_cls: MagicMock, db: MeshDatabase) -> None:
        _seed_linear_topology(db)
        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        exec_result = manager.execute_failover("!b")

        # Mark failed node as online
        with db.connection() as conn:
            conn.execute("UPDATE devices SET mesh_status = 'online' WHERE node_id = '!b'")

        recovery = manager.check_recoveries()
        assert recovery["checked"] == 1
        assert recovery["recovered"] == 1
        assert recovery["reverted"] == 1

        # Event should be reverted
        event = db.get_failover_event(exec_result["event_id"])
        assert event["status"] == "reverted"

    @patch("jenn_mesh.core.failover_manager.RemoteAdmin")
    def test_no_revert_when_still_offline(
        self, mock_admin_cls: MagicMock, db: MeshDatabase
    ) -> None:
        _seed_linear_topology(db)
        # Mark node as offline
        with db.connection() as conn:
            conn.execute("UPDATE devices SET mesh_status = 'offline' WHERE node_id = '!b'")

        mock_admin = MagicMock()
        mock_admin.set_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!a", command="set", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = FailoverManager(db)
        manager.execute_failover("!b")

        recovery = manager.check_recoveries()
        assert recovery["recovered"] == 0
        assert recovery["reverted"] == 0

    def test_no_active_failovers(self, db: MeshDatabase) -> None:
        manager = FailoverManager(db)
        recovery = manager.check_recoveries()
        assert recovery["checked"] == 0
        assert recovery["recovered"] == 0


# ── DB failover CRUD methods ────────────────────────────────────────


class TestDBFailoverMethods:
    """Tests for the raw database failover methods."""

    def test_create_and_get_event(self, db: MeshDatabase) -> None:
        db.upsert_device("!a")
        event_id = db.create_failover_event("!a", '["!b", "!c"]', "test")
        event = db.get_failover_event(event_id)
        assert event is not None
        assert event["failed_node_id"] == "!a"
        assert event["status"] == "active"
        assert json.loads(event["dependent_nodes"]) == ["!b", "!c"]

    def test_get_active_failover_for_node(self, db: MeshDatabase) -> None:
        db.upsert_device("!a")
        db.create_failover_event("!a", "[]", "test")
        event = db.get_active_failover_for_node("!a")
        assert event is not None
        assert event["failed_node_id"] == "!a"

    def test_list_active_events(self, db: MeshDatabase) -> None:
        db.upsert_device("!a")
        db.upsert_device("!b")
        db.create_failover_event("!a", "[]", "test")
        db.create_failover_event("!b", "[]", "test")
        events = db.list_active_failover_events()
        assert len(events) == 2

    def test_update_event_status(self, db: MeshDatabase) -> None:
        db.upsert_device("!a")
        event_id = db.create_failover_event("!a", "[]", "test")
        db.update_failover_event_status(event_id, "reverted", reverted_at="2024-01-01T00:00:00Z")
        event = db.get_failover_event(event_id)
        assert event["status"] == "reverted"
        assert event["reverted_at"] == "2024-01-01T00:00:00Z"

    def test_create_and_get_compensation(self, db: MeshDatabase) -> None:
        db.upsert_device("!a")
        db.upsert_device("!b")
        event_id = db.create_failover_event("!a", "[]", "test")
        db.create_failover_compensation(
            event_id, "!b", "tx_power_increase", "lora.tx_power", "17", "30"
        )
        comps = db.get_compensations_for_event(event_id)
        assert len(comps) == 1
        assert comps[0]["comp_node_id"] == "!b"
        assert comps[0]["original_value"] == "17"
        assert comps[0]["new_value"] == "30"
        assert comps[0]["status"] == "pending"

    def test_update_compensation_status_applied(self, db: MeshDatabase) -> None:
        db.upsert_device("!a")
        db.upsert_device("!b")
        event_id = db.create_failover_event("!a", "[]", "test")
        comp_id = db.create_failover_compensation(
            event_id, "!b", "tx_power_increase", "lora.tx_power", "17", "30"
        )
        db.update_compensation_status(comp_id, "applied")
        comps = db.get_compensations_for_event(event_id)
        assert comps[0]["status"] == "applied"
        assert comps[0]["applied_at"] is not None

    def test_update_compensation_status_reverted(self, db: MeshDatabase) -> None:
        db.upsert_device("!a")
        db.upsert_device("!b")
        event_id = db.create_failover_event("!a", "[]", "test")
        comp_id = db.create_failover_compensation(
            event_id, "!b", "tx_power_increase", "lora.tx_power", "17", "30"
        )
        db.update_compensation_status(comp_id, "applied")
        db.update_compensation_status(comp_id, "reverted")
        comps = db.get_compensations_for_event(event_id)
        assert comps[0]["status"] == "reverted"
        assert comps[0]["reverted_at"] is not None

    def test_schema_version_is_9(self, db: MeshDatabase) -> None:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
        assert row["version"] == 9
