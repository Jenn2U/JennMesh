"""Tests for bulk operation manager — preview, execute, cancel, progress."""

from __future__ import annotations

import json
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.core.bulk_operation_manager import (
    BulkOperationManager,
    _resolve_targets,
)
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db(tmp_path) -> MeshDatabase:
    db = MeshDatabase(db_path=str(tmp_path / "bulk_test.db"))
    db.upsert_device(
        "!b001",
        long_name="R1",
        role="ROUTER",
        hw_model="heltec_v3",
        firmware_version="2.5.6",
        mesh_status="reachable",
    )
    db.upsert_device(
        "!b002",
        long_name="R2",
        role="ROUTER",
        hw_model="heltec_v3",
        firmware_version="2.5.6",
        mesh_status="reachable",
    )
    db.upsert_device(
        "!b003",
        long_name="M1",
        role="CLIENT",
        hw_model="tbeam",
        firmware_version="2.4.2",
        mesh_status="unknown",
    )
    db.upsert_device(
        "!b004",
        long_name="S1",
        role="SENSOR",
        hw_model="rak4631",
        firmware_version="2.5.0",
    )
    return db


@pytest.fixture
def manager(db) -> BulkOperationManager:
    return BulkOperationManager(db=db)


# ── _resolve_targets() ───────────────────────────────────────────────


class TestResolveTargets:
    def test_all_devices(self, db):
        targets = _resolve_targets(db, {"all_devices": True})
        assert len(targets) == 4

    def test_explicit_node_ids(self, db):
        targets = _resolve_targets(db, {"node_ids": ["!b001", "!b002"]})
        assert targets == ["!b001", "!b002"]

    def test_filter_by_role(self, db):
        targets = _resolve_targets(db, {"role": "ROUTER"})
        assert targets == ["!b001", "!b002"]

    def test_filter_by_hardware(self, db):
        targets = _resolve_targets(db, {"hardware_model": "tbeam"})
        assert targets == ["!b003"]

    def test_filter_by_firmware(self, db):
        targets = _resolve_targets(db, {"firmware_version": "2.5.6"})
        assert set(targets) == {"!b001", "!b002"}

    def test_filter_by_mesh_status(self, db):
        targets = _resolve_targets(db, {"mesh_status": "reachable"})
        assert set(targets) == {"!b001", "!b002"}

    def test_and_filter_combination(self, db):
        targets = _resolve_targets(
            db,
            {
                "hardware_model": "heltec_v3",
                "mesh_status": "reachable",
            },
        )
        assert set(targets) == {"!b001", "!b002"}

    def test_no_match_returns_empty(self, db):
        targets = _resolve_targets(db, {"role": "NONEXISTENT"})
        assert targets == []

    def test_empty_filter_returns_all(self, db):
        """Empty filter with no specific criteria returns all devices."""
        targets = _resolve_targets(db, {})
        assert len(targets) == 4


# ── Preview ───────────────────────────────────────────────────────────


class TestPreview:
    def test_preview_returns_targets(self, manager):
        result = manager.preview(
            {
                "operation_type": "reboot",
                "target_filter": {"all_devices": True},
            }
        )
        assert result["status"] == "preview"
        assert result["target_count"] == 4
        assert result["operation_type"] == "reboot"

    def test_preview_factory_reset_warning(self, manager):
        result = manager.preview(
            {
                "operation_type": "factory_reset",
                "target_filter": {"all_devices": True},
            }
        )
        warnings = result.get("warnings", [])
        assert any("factory reset" in w.lower() for w in warnings)

    def test_preview_no_targets_warning(self, manager):
        result = manager.preview(
            {
                "operation_type": "reboot",
                "target_filter": {"role": "NONEXISTENT"},
            }
        )
        assert result["target_count"] == 0
        warnings = result.get("warnings", [])
        assert any("no devices" in w.lower() for w in warnings)

    def test_preview_large_operation_warning(self, manager, db):
        # Add many devices
        for i in range(55):
            db.upsert_device(f"!extra{i:04d}", long_name=f"E{i}")
        result = manager.preview(
            {
                "operation_type": "reboot",
                "target_filter": {"all_devices": True},
            }
        )
        warnings = result.get("warnings", [])
        assert any("large operation" in w.lower() for w in warnings)

    def test_preview_stores_in_db(self, manager, db):
        result = manager.preview(
            {
                "operation_type": "reboot",
                "target_filter": {"role": "ROUTER"},
            }
        )
        op = db.get_bulk_operation(result["id"])
        assert op is not None
        assert op["status"] == "preview"


# ── Execute ───────────────────────────────────────────────────────────


class TestExecute:
    def test_execute_rejects_dry_run(self, manager):
        result = manager.execute(
            {
                "operation_type": "reboot",
                "target_filter": {"all_devices": True},
                "dry_run": True,
            }
        )
        assert "error" in result

    def test_execute_rejects_unconfirmed(self, manager):
        result = manager.execute(
            {
                "operation_type": "reboot",
                "target_filter": {"all_devices": True},
                "dry_run": False,
                "confirmed": False,
            }
        )
        assert "error" in result

    def test_execute_rejects_no_targets(self, manager):
        result = manager.execute(
            {
                "operation_type": "reboot",
                "target_filter": {"role": "NONEXISTENT"},
                "dry_run": False,
                "confirmed": True,
            }
        )
        assert "error" in result

    def test_execute_reboot_starts(self, manager, db):
        result = manager.execute(
            {
                "operation_type": "reboot",
                "target_filter": {"role": "ROUTER"},
                "dry_run": False,
                "confirmed": True,
            }
        )
        assert result["status"] == "running"
        assert result["target_count"] == 2
        # Wait for background thread
        time.sleep(0.5)
        op = db.get_bulk_operation(result["id"])
        assert op["status"] in ("completed", "running")

    def test_execute_psk_rotation_needs_psk(self, manager, db):
        result = manager.execute(
            {
                "operation_type": "psk_rotation",
                "target_filter": {"role": "ROUTER"},
                "parameters": {},  # missing new_psk
                "dry_run": False,
                "confirmed": True,
            }
        )
        # Should still start — failures happen per-device in background
        assert result["status"] == "running"
        time.sleep(0.5)
        op = db.get_bulk_operation(result["id"])
        # All failed because no new_psk
        assert op["failed_count"] == 2


# ── Cancel ────────────────────────────────────────────────────────────


class TestCancel:
    def test_cancel_nonexistent(self, manager):
        result = manager.cancel(9999)
        assert "error" in result

    def test_cancel_preview(self, manager):
        preview = manager.preview(
            {
                "operation_type": "reboot",
                "target_filter": {"all_devices": True},
            }
        )
        # Cancel a preview — should work since it's not completed
        result = manager.cancel(preview["id"])
        assert result["status"] == "cancelled"


# ── Progress ──────────────────────────────────────────────────────────


class TestProgress:
    def test_get_progress_nonexistent(self, manager):
        assert manager.get_progress(9999) is None

    def test_get_progress_after_execute(self, manager, db):
        result = manager.execute(
            {
                "operation_type": "reboot",
                "target_filter": {"node_ids": ["!b001"]},
                "dry_run": False,
                "confirmed": True,
            }
        )
        time.sleep(0.5)
        progress = manager.get_progress(result["id"])
        assert progress is not None
        assert progress["completed_count"] >= 0


# ── List ──────────────────────────────────────────────────────────────


class TestListOperations:
    def test_list_empty(self, manager):
        assert manager.list_operations() == []

    def test_list_after_previews(self, manager):
        manager.preview({"operation_type": "reboot", "target_filter": {"all_devices": True}})
        manager.preview({"operation_type": "reboot", "target_filter": {"role": "ROUTER"}})
        ops = manager.list_operations()
        assert len(ops) == 2

    def test_list_with_status_filter(self, manager, db):
        manager.preview({"operation_type": "reboot", "target_filter": {"all_devices": True}})
        ops = manager.list_operations(status="preview")
        assert len(ops) == 1
        ops = manager.list_operations(status="running")
        assert len(ops) == 0
