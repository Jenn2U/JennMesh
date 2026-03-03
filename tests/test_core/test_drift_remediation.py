"""Tests for DriftRemediationManager — preview, remediate, status."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.agent.remote_admin import RemoteAdminResult
from jenn_mesh.core.drift_remediation import DriftRemediationManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.device import ConfigHash


@pytest.fixture()
def db(tmp_path):
    """Fresh DB for each test."""
    db_path = str(tmp_path / "test.db")
    return MeshDatabase(db_path=db_path)


@pytest.fixture()
def sample_yaml():
    return "owner:\n  long_name: TestRelay\nradio:\n  role: ROUTER\n"


@pytest.fixture()
def sample_hash(sample_yaml):
    return ConfigHash.compute(sample_yaml)


def _seed_drifted_device(db, node_id, template_role, sample_yaml, sample_hash):
    """Seed a device with drift (config_hash ≠ template_hash)."""
    db.upsert_device(node_id, long_name="Test-Device")
    db.save_config_template(template_role, sample_yaml, sample_hash)
    with db.connection() as conn:
        conn.execute(
            """UPDATE devices SET template_role = ?, config_hash = ?, template_hash = ?
               WHERE node_id = ?""",
            (template_role, "drifted-hash-000", sample_hash, node_id),
        )


def _seed_matching_device(db, node_id, template_role, sample_yaml, sample_hash):
    """Seed a device with no drift (config_hash == template_hash)."""
    db.upsert_device(node_id, long_name="Clean-Device")
    db.save_config_template(template_role, sample_yaml, sample_hash)
    with db.connection() as conn:
        conn.execute(
            """UPDATE devices SET template_role = ?, config_hash = ?, template_hash = ?
               WHERE node_id = ?""",
            (template_role, sample_hash, sample_hash, node_id),
        )


# ── Preview tests ──────────────────────────────────────────────────────


class TestPreviewRemediation:
    def test_preview_drifted_device(self, db, sample_yaml, sample_hash):
        _seed_drifted_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)
        manager = DriftRemediationManager(db=db)
        preview = manager.preview_remediation("!aaa11111")
        assert preview["node_id"] == "!aaa11111"
        assert preview["template_role"] == "relay-node"
        assert preview["template_yaml"] == sample_yaml
        assert preview["device_hash"] == "drifted-hash-000"
        assert preview["template_hash"] == sample_hash
        assert preview["drifted"] is True

    def test_preview_not_drifted(self, db, sample_yaml, sample_hash):
        _seed_matching_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)
        manager = DriftRemediationManager(db=db)
        preview = manager.preview_remediation("!aaa11111")
        assert preview["drifted"] is False

    def test_preview_device_not_found(self, db):
        manager = DriftRemediationManager(db=db)
        with pytest.raises(ValueError, match="not found"):
            manager.preview_remediation("!unknown")

    def test_preview_no_template_assigned(self, db):
        db.upsert_device("!aaa11111", long_name="No-Template")
        manager = DriftRemediationManager(db=db)
        with pytest.raises(ValueError, match="no template_role"):
            manager.preview_remediation("!aaa11111")

    def test_preview_template_not_found(self, db):
        db.upsert_device("!aaa11111", long_name="Bad-Template")
        with db.connection() as conn:
            conn.execute(
                "UPDATE devices SET template_role = ? WHERE node_id = ?",
                ("nonexistent-role", "!aaa11111"),
            )
        manager = DriftRemediationManager(db=db)
        with pytest.raises(ValueError, match="not found"):
            manager.preview_remediation("!aaa11111")


# ── Remediate device tests ─────────────────────────────────────────────


class TestRemediateDevice:
    @patch("jenn_mesh.core.drift_remediation.RemoteAdmin")
    def test_successful_remediation(self, mock_admin_cls, db, sample_yaml, sample_hash):
        """Mock RemoteAdmin success → status=delivered, hash updated, log written."""
        _seed_drifted_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)
        mock_admin = MagicMock()
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!aaa11111", command="configure", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = DriftRemediationManager(db=db)
        result = manager.remediate_device("!aaa11111", operator="test-op")

        assert result["status"] == "delivered"
        assert result["template_role"] == "relay-node"

        # Verify DB hash was updated to match template
        device = db.get_device("!aaa11111")
        assert device["config_hash"] == sample_hash
        assert device["template_hash"] == sample_hash

        # Verify provisioning log entry
        logs = db.get_provisioning_log_for_node("!aaa11111", "drift_remediation")
        assert len(logs) == 1
        assert "Successfully pushed" in logs[0]["details"]
        assert logs[0]["operator"] == "test-op"

    @patch("jenn_mesh.core.drift_remediation.RemoteAdmin")
    def test_failed_with_queue(self, mock_admin_cls, db, sample_yaml, sample_hash):
        """Mock failure + config_queue → status=queued, enqueue called."""
        _seed_drifted_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)
        mock_admin = MagicMock()
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=False, node_id="!aaa11111", command="configure", error="Timeout"
        )
        mock_admin_cls.return_value = mock_admin

        mock_queue = MagicMock()
        manager = DriftRemediationManager(db=db, config_queue=mock_queue)
        result = manager.remediate_device("!aaa11111")

        assert result["status"] == "queued"
        mock_queue.enqueue.assert_called_once()
        call_kwargs = mock_queue.enqueue.call_args
        assert call_kwargs[1]["target_node_id"] == "!aaa11111"
        assert call_kwargs[1]["template_role"] == "relay-node"

    @patch("jenn_mesh.core.drift_remediation.RemoteAdmin")
    def test_failed_without_queue(self, mock_admin_cls, db, sample_yaml, sample_hash):
        """Mock failure, no config_queue → status=failed."""
        _seed_drifted_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)
        mock_admin = MagicMock()
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=False, node_id="!aaa11111", command="configure", error="Timeout"
        )
        mock_admin_cls.return_value = mock_admin

        manager = DriftRemediationManager(db=db, config_queue=None)
        result = manager.remediate_device("!aaa11111")

        assert result["status"] == "failed"

    def test_remediate_device_not_found(self, db):
        manager = DriftRemediationManager(db=db)
        with pytest.raises(ValueError, match="not found"):
            manager.remediate_device("!unknown")

    @patch("jenn_mesh.core.drift_remediation.RemoteAdmin")
    def test_resolves_both_alert_types(self, mock_admin_cls, db, sample_yaml, sample_hash):
        """Successful remediation resolves CONFIG_DRIFT and CONFIG_PUSH_FAILED alerts."""
        _seed_drifted_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)

        # Create both alert types
        db.create_alert("!aaa11111", "config_drift", "warning", "Drifted")
        db.create_alert("!aaa11111", "config_push_failed", "warning", "Push failed")

        mock_admin = MagicMock()
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!aaa11111", command="configure", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = DriftRemediationManager(db=db)
        result = manager.remediate_device("!aaa11111")

        assert result["status"] == "delivered"

        # Both alerts should be resolved
        alerts = db.get_active_alerts("!aaa11111")
        config_alerts = [
            a for a in alerts if a["alert_type"] in ("config_drift", "config_push_failed")
        ]
        assert len(config_alerts) == 0

    @patch("jenn_mesh.core.drift_remediation.RemoteAdmin")
    def test_remediate_no_template_role(self, mock_admin_cls, db):
        """Device with no template_role raises ValueError."""
        db.upsert_device("!aaa11111", long_name="NoRole")
        manager = DriftRemediationManager(db=db)
        with pytest.raises(ValueError, match="no template_role"):
            manager.remediate_device("!aaa11111")

    @patch("jenn_mesh.core.drift_remediation.RemoteAdmin")
    def test_failure_logs_provisioning(self, mock_admin_cls, db, sample_yaml, sample_hash):
        """Failed remediation still writes provisioning log entry."""
        _seed_drifted_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)
        mock_admin = MagicMock()
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=False, node_id="!aaa11111", command="configure", error="No route"
        )
        mock_admin_cls.return_value = mock_admin

        manager = DriftRemediationManager(db=db, config_queue=None)
        manager.remediate_device("!aaa11111", operator="op-fail")

        logs = db.get_provisioning_log_for_node("!aaa11111", "drift_remediation")
        assert len(logs) == 1
        assert "failed" in logs[0]["details"]
        assert logs[0]["operator"] == "op-fail"


# ── Remediate all tests ────────────────────────────────────────────────


class TestRemediateAll:
    @patch("jenn_mesh.core.drift_remediation.RemoteAdmin")
    def test_remediate_all_with_drifted(self, mock_admin_cls, db, sample_yaml, sample_hash):
        """Two drifted devices, both succeed."""
        _seed_drifted_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)
        _seed_drifted_device(db, "!bbb22222", "relay-node", sample_yaml, sample_hash)

        mock_admin = MagicMock()
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="mock", command="configure", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        manager = DriftRemediationManager(db=db)
        result = manager.remediate_all(operator="batch-op")

        assert result["total"] == 2
        assert result["delivered"] == 2
        assert result["queued"] == 0
        assert result["failed"] == 0
        assert len(result["results"]) == 2

    def test_remediate_all_empty(self, db):
        """No drifted devices → total=0."""
        manager = DriftRemediationManager(db=db)
        result = manager.remediate_all()
        assert result["total"] == 0
        assert result["delivered"] == 0
        assert len(result["results"]) == 0

    @patch("jenn_mesh.core.drift_remediation.RemoteAdmin")
    def test_remediate_all_mixed_results(self, mock_admin_cls, db, sample_yaml, sample_hash):
        """One succeeds, one fails → correct counts."""
        _seed_drifted_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)
        _seed_drifted_device(db, "!bbb22222", "relay-node", sample_yaml, sample_hash)

        mock_admin = MagicMock()
        # First call succeeds, second fails
        mock_admin.apply_remote_config.side_effect = [
            RemoteAdminResult(success=True, node_id="!aaa11111", command="configure", output="OK"),
            RemoteAdminResult(
                success=False, node_id="!bbb22222", command="configure", error="Timeout"
            ),
        ]
        mock_admin_cls.return_value = mock_admin

        manager = DriftRemediationManager(db=db, config_queue=None)
        result = manager.remediate_all()

        assert result["total"] == 2
        assert result["delivered"] == 1
        assert result["failed"] == 1


# ── Remediation status tests ──────────────────────────────────────────


class TestGetRemediationStatus:
    def test_status_with_data(self, db, sample_yaml, sample_hash):
        """Device with drift + alerts + log → all fields populated."""
        _seed_drifted_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)
        db.create_alert("!aaa11111", "config_drift", "warning", "Drifted")
        db.log_provisioning("!aaa11111", "drift_remediation", "relay-node", details="Test")

        manager = DriftRemediationManager(db=db)
        status = manager.get_remediation_status("!aaa11111")

        assert status["node_id"] == "!aaa11111"
        assert status["drifted"] is True
        assert status["template_role"] == "relay-node"
        assert status["pending_queue_entries"] == 0
        assert len(status["active_alerts"]) == 1
        assert status["active_alerts"][0]["alert_type"] == "config_drift"
        assert len(status["recent_remediation_log"]) == 1

    def test_status_device_not_found(self, db):
        manager = DriftRemediationManager(db=db)
        with pytest.raises(ValueError, match="not found"):
            manager.get_remediation_status("!unknown")

    def test_status_no_queue_manager(self, db, sample_yaml, sample_hash):
        """Manager with config_queue=None → pending_queue_entries=0."""
        _seed_drifted_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)
        manager = DriftRemediationManager(db=db, config_queue=None)
        status = manager.get_remediation_status("!aaa11111")
        assert status["pending_queue_entries"] == 0

    def test_status_with_queue_manager(self, db, sample_yaml, sample_hash):
        """Manager with config_queue wired → queries queue status."""
        _seed_drifted_device(db, "!aaa11111", "relay-node", sample_yaml, sample_hash)
        mock_queue = MagicMock()
        mock_queue.get_device_queue_status.return_value = {
            "pending": 2,
            "retrying": 1,
            "delivered": 5,
        }
        manager = DriftRemediationManager(db=db, config_queue=mock_queue)
        status = manager.get_remediation_status("!aaa11111")
        assert status["pending_queue_entries"] == 3  # 2 + 1
