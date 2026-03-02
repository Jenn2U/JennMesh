"""Tests for bulk push manager — push golden templates to fleet devices."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.agent.remote_admin import RemoteAdminResult
from jenn_mesh.core.bulk_push import BulkPushManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.workbench import (
    BulkPushProgress,
    BulkPushRequest,
    PushDeviceStatus,
)


@pytest.fixture
def configs_dir(tmp_path: Path) -> Path:
    """Isolated configs directory with a test template."""
    d = tmp_path / "configs"
    d.mkdir()
    (d / "relay-node.yaml").write_text("device:\n  role: 4\nlora:\n  region: 1\n")
    return d


@pytest.fixture
def bulk_mgr(db: MeshDatabase, configs_dir: Path) -> BulkPushManager:
    """BulkPushManager wired to test DB and temp configs."""
    # Seed the template in the DB so validation passes
    db.save_config_template(
        role="relay-node",
        yaml_content="device:\n  role: 4\nlora:\n  region: 1\n",
        config_hash="a" * 64,
    )
    return BulkPushManager(db, configs_dir=configs_dir)


class TestStartPush:
    def test_start_push_creates_progress(self, bulk_mgr: BulkPushManager):
        """Dry run should return progress with all devices SKIPPED."""
        request = BulkPushRequest(
            template_name="relay-node",
            device_ids=["!aaa11111", "!bbb22222"],
            dry_run=True,
        )
        progress = bulk_mgr.start_push(request)

        assert isinstance(progress, BulkPushProgress)
        assert progress.template_name == "relay-node"
        assert progress.total == 2
        assert progress.is_complete is True
        assert progress.skipped == 2
        assert progress.queued == 0

    def test_start_push_validates_template(self, bulk_mgr: BulkPushManager):
        """Should raise ValueError for unknown template."""
        request = BulkPushRequest(
            template_name="nonexistent-template",
            device_ids=["!aaa11111"],
        )
        with pytest.raises(ValueError, match="not found"):
            bulk_mgr.start_push(request)

    def test_start_push_validates_devices(self, bulk_mgr: BulkPushManager):
        """Should raise ValueError when no devices specified."""
        request = BulkPushRequest(
            template_name="relay-node",
            device_ids=[],
        )
        with pytest.raises(ValueError, match="No target devices"):
            bulk_mgr.start_push(request)


class TestExecutePush:
    @patch("jenn_mesh.core.bulk_push.RemoteAdmin")
    def test_push_success_all_devices(self, mock_admin_cls, bulk_mgr: BulkPushManager):
        """All devices should succeed when remote admin succeeds."""
        mock_admin = MagicMock()
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="test", command="configure", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        request = BulkPushRequest(
            template_name="relay-node",
            device_ids=["!aaa11111", "!bbb22222"],
            dry_run=False,
        )
        progress = bulk_mgr.start_push(request)

        # Wait for background thread to finish (should be fast with mocks)
        _wait_for_completion(bulk_mgr, progress.push_id, timeout=5)

        result = bulk_mgr.get_progress(progress.push_id)
        assert result is not None
        assert result.is_complete is True
        assert result.success == 2
        assert result.failed == 0

    @patch("jenn_mesh.core.bulk_push.RemoteAdmin")
    def test_push_partial_failure(self, mock_admin_cls, bulk_mgr: BulkPushManager):
        """One device fails, others succeed."""
        mock_admin = MagicMock()

        def side_effect(node_id, path):
            if node_id == "!bbb22222":
                return RemoteAdminResult(
                    success=False,
                    node_id=node_id,
                    command="configure",
                    error="Timeout",
                )
            return RemoteAdminResult(
                success=True,
                node_id=node_id,
                command="configure",
                output="OK",
            )

        mock_admin.apply_remote_config.side_effect = side_effect
        mock_admin_cls.return_value = mock_admin

        request = BulkPushRequest(
            template_name="relay-node",
            device_ids=["!aaa11111", "!bbb22222", "!ccc33333"],
            dry_run=False,
        )
        progress = bulk_mgr.start_push(request)
        _wait_for_completion(bulk_mgr, progress.push_id, timeout=5)

        result = bulk_mgr.get_progress(progress.push_id)
        assert result is not None
        assert result.success == 2
        assert result.failed == 1

        # Check the failed device has an error
        failed_devs = [d for d in result.devices if d.status == PushDeviceStatus.FAILED]
        assert len(failed_devs) == 1
        assert failed_devs[0].node_id == "!bbb22222"
        assert "Timeout" in (failed_devs[0].error or "")

    @patch("jenn_mesh.core.bulk_push.RemoteAdmin")
    def test_push_logs_audit_trail(
        self, mock_admin_cls, bulk_mgr: BulkPushManager, db: MeshDatabase
    ):
        """Push should write provisioning log entries."""
        mock_admin = MagicMock()
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="test", command="configure", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        request = BulkPushRequest(
            template_name="relay-node",
            device_ids=["!aaa11111"],
            dry_run=False,
        )
        progress = bulk_mgr.start_push(request)
        _wait_for_completion(bulk_mgr, progress.push_id, timeout=5)

        with db.connection() as conn:
            logs = conn.execute(
                "SELECT * FROM provisioning_log WHERE action = 'bulk_push'"
            ).fetchall()
        assert len(logs) >= 1


class TestGetProgress:
    def test_get_progress_exists(self, bulk_mgr: BulkPushManager):
        request = BulkPushRequest(
            template_name="relay-node",
            device_ids=["!aaa11111"],
            dry_run=True,
        )
        progress = bulk_mgr.start_push(request)
        result = bulk_mgr.get_progress(progress.push_id)
        assert result is not None
        assert result.push_id == progress.push_id

    def test_get_progress_not_found(self, bulk_mgr: BulkPushManager):
        assert bulk_mgr.get_progress("nonexistent") is None


class TestCancelPush:
    def test_cancel_nonexistent(self, bulk_mgr: BulkPushManager):
        assert bulk_mgr.cancel_push("nonexistent") is False

    @patch("jenn_mesh.core.bulk_push.RemoteAdmin")
    def test_cancel_running_push(self, mock_admin_cls, bulk_mgr: BulkPushManager):
        """Cancellation should mark remaining devices as SKIPPED."""
        call_count = 0

        def slow_push(node_id, path):
            nonlocal call_count
            call_count += 1
            time.sleep(0.1)  # Simulate slow mesh push
            return RemoteAdminResult(
                success=True, node_id=node_id, command="configure", output="OK"
            )

        mock_admin = MagicMock()
        mock_admin.apply_remote_config.side_effect = slow_push
        mock_admin_cls.return_value = mock_admin

        request = BulkPushRequest(
            template_name="relay-node",
            device_ids=["!dev1", "!dev2", "!dev3", "!dev4", "!dev5"],
            dry_run=False,
        )
        progress = bulk_mgr.start_push(request)

        # Let one push complete then cancel
        time.sleep(0.15)
        bulk_mgr.cancel_push(progress.push_id)

        _wait_for_completion(bulk_mgr, progress.push_id, timeout=5)

        result = bulk_mgr.get_progress(progress.push_id)
        assert result is not None
        assert result.is_complete is True
        assert result.skipped > 0
        assert "Cancelled" in (result.error or "")


class TestListPushes:
    def test_list_pushes(self, bulk_mgr: BulkPushManager):
        bulk_mgr.start_push(
            BulkPushRequest(
                template_name="relay-node",
                device_ids=["!a"],
                dry_run=True,
            )
        )
        bulk_mgr.start_push(
            BulkPushRequest(
                template_name="relay-node",
                device_ids=["!b"],
                dry_run=True,
            )
        )
        pushes = bulk_mgr.list_pushes()
        assert len(pushes) == 2


# ── Helpers ──────────────────────────────────────────────────────────


def _wait_for_completion(mgr: BulkPushManager, push_id: str, timeout: float = 5.0) -> None:
    """Poll until push completes or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        progress = mgr.get_progress(push_id)
        if progress and progress.is_complete:
            return
        time.sleep(0.05)
    raise TimeoutError(f"Push {push_id} did not complete within {timeout}s")
