"""Tests for the edge association manager — JennEdge ↔ mesh radio cross-ref."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta

import pytest

from jenn_mesh.core.edge_association_manager import EdgeAssociationManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.edge_association import AssociationStatus


@pytest.fixture
def db(tmp_path) -> MeshDatabase:
    return MeshDatabase(db_path=str(tmp_path / "edge_assoc_test.db"))


@pytest.fixture
def manager(db) -> EdgeAssociationManager:
    return EdgeAssociationManager(db=db)


# ── Create association ───────────────────────────────────────────────


class TestCreateAssociation:
    def test_create_basic(self, manager):
        assoc = manager.create_association(
            edge_device_id="edge-001",
            node_id="!abc123",
        )
        assert assoc.id is not None
        assert assoc.edge_device_id == "edge-001"
        assert assoc.node_id == "!abc123"
        assert assoc.association_type == "co-located"

    def test_create_with_details(self, manager):
        assoc = manager.create_association(
            edge_device_id="edge-002",
            node_id="!def456",
            edge_hostname="pi4-field-02",
            edge_ip="10.10.50.22",
            association_type="usb-connected",
        )
        assert assoc.edge_hostname == "pi4-field-02"
        assert assoc.edge_ip == "10.10.50.22"
        assert assoc.association_type == "usb-connected"

    def test_empty_edge_id_raises(self, manager):
        with pytest.raises(ValueError, match="edge_device_id is required"):
            manager.create_association(edge_device_id="", node_id="!abc")

    def test_empty_node_id_raises(self, manager):
        with pytest.raises(ValueError, match="node_id is required"):
            manager.create_association(edge_device_id="edge-001", node_id="")

    def test_duplicate_edge_raises(self, manager):
        manager.create_association(edge_device_id="edge-001", node_id="!abc")
        with pytest.raises(ValueError, match="already has an association"):
            manager.create_association(edge_device_id="edge-001", node_id="!def")


# ── Lookup ───────────────────────────────────────────────────────────


class TestLookup:
    def test_get_by_edge(self, manager):
        manager.create_association(edge_device_id="edge-001", node_id="!abc")
        assoc = manager.get_by_edge("edge-001")
        assert assoc is not None
        assert assoc["node_id"] == "!abc"

    def test_get_by_node(self, manager):
        manager.create_association(edge_device_id="edge-001", node_id="!abc")
        assoc = manager.get_by_node("!abc")
        assert assoc is not None
        assert assoc["edge_device_id"] == "edge-001"

    def test_get_by_edge_not_found(self, manager):
        assert manager.get_by_edge("nonexistent") is None

    def test_get_by_node_not_found(self, manager):
        assert manager.get_by_node("!nonexistent") is None


# ── List / update / delete ───────────────────────────────────────────


class TestCRUD:
    def test_list_all(self, manager):
        manager.create_association(edge_device_id="a", node_id="!1")
        manager.create_association(edge_device_id="b", node_id="!2")
        assocs = manager.list_associations()
        assert len(assocs) == 2

    def test_list_by_status(self, manager):
        manager.create_association(edge_device_id="a", node_id="!1")
        manager.create_association(edge_device_id="b", node_id="!2")
        manager.update_association("b", status="stale")
        active = manager.list_associations(status="active")
        assert len(active) == 1
        assert active[0]["edge_device_id"] == "a"

    def test_update_association(self, manager):
        manager.create_association(edge_device_id="edge-001", node_id="!abc")
        assert manager.update_association("edge-001", edge_hostname="new-host")
        assoc = manager.get_by_edge("edge-001")
        assert assoc["edge_hostname"] == "new-host"

    def test_delete_association(self, manager):
        manager.create_association(edge_device_id="edge-001", node_id="!abc")
        assert manager.delete_association("edge-001")
        assert manager.get_by_edge("edge-001") is None

    def test_delete_nonexistent(self, manager):
        assert not manager.delete_association("nonexistent")


# ── Combined status ──────────────────────────────────────────────────


class TestCombinedStatus:
    def test_status_with_device(self, manager, db):
        db.upsert_device(
            "!abc123",
            long_name="Radio-1",
            battery_level=75,
            signal_rssi=-85,
            signal_snr=10.0,
            latitude=30.267,
            longitude=-97.743,
            mesh_status="reachable",
            last_seen=datetime.utcnow().isoformat(),
        )
        manager.create_association(edge_device_id="edge-001", node_id="!abc123")
        status = manager.get_combined_status("edge-001")
        assert status is not None
        assert status.edge_device_id == "edge-001"
        assert status.node_id == "!abc123"
        assert status.radio_online is True
        assert status.radio_battery == 75
        assert status.radio_signal_rssi == -85

    def test_status_radio_offline(self, manager, db):
        db.upsert_device("!abc123", mesh_status="unreachable")
        manager.create_association(edge_device_id="edge-001", node_id="!abc123")
        status = manager.get_combined_status("edge-001")
        assert status is not None
        assert status.radio_online is False

    def test_status_not_found(self, manager):
        assert manager.get_combined_status("nonexistent") is None


# ── Stale detection ──────────────────────────────────────────────────


class TestStaleDetection:
    def test_no_associations(self, manager):
        assert manager.update_stale_associations() == 0

    def test_marks_stale_when_no_device(self, manager, db):
        manager.create_association(edge_device_id="edge-001", node_id="!ghost")
        count = manager.update_stale_associations()
        assert count == 1
        assoc = manager.get_by_edge("edge-001")
        assert assoc["status"] == "stale"

    def test_marks_stale_when_old(self, manager, db):
        old_time = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        db.upsert_device("!abc", last_seen=old_time)
        manager.create_association(edge_device_id="edge-001", node_id="!abc")
        count = manager.update_stale_associations()
        assert count == 1

    def test_keeps_active_when_recent(self, manager, db):
        recent = datetime.utcnow().isoformat()
        db.upsert_device("!abc", last_seen=recent)
        manager.create_association(edge_device_id="edge-001", node_id="!abc")
        count = manager.update_stale_associations()
        assert count == 0
        assoc = manager.get_by_edge("edge-001")
        assert assoc["status"] == "active"
