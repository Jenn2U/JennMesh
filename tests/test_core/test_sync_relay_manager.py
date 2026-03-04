"""Tests for SyncRelayManager — gateway-side CRDT sync relay (MESH-027)."""

from __future__ import annotations

import base64
import json
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.core.sync_relay_manager import SyncRelayManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.sync_relay import (
    compute_crc16,
    format_sync_ack,
    format_sync_frag,
    format_sync_nack,
    format_sync_sv,
)

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db() -> MeshDatabase:
    """Fresh test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def bridge() -> MagicMock:
    """Mock RadioBridge with send_text."""
    mock = MagicMock()
    mock.send_text.return_value = True
    return mock


@pytest.fixture
def http_client() -> MagicMock:
    """Mock HTTP client for Production API calls."""
    mock = MagicMock()
    return mock


def _make_response(status_code: int = 200, data: dict | None = None) -> MagicMock:
    """Create a mock HTTP response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data or {}
    return resp


@pytest.fixture
def manager(db: MeshDatabase, bridge: MagicMock, http_client: MagicMock) -> SyncRelayManager:
    """SyncRelayManager with all dependencies mocked."""
    return SyncRelayManager(
        db=db,
        bridge=bridge,
        production_url="https://jenn2u.ai",
        sync_token="test-token-123",
        cooldown_minutes=10,
        http_client=http_client,
    )


def _seed_device(db: MeshDatabase, node_id: str = "!a") -> None:
    """Seed a device so alerts can be created."""
    db.upsert_device(node_id, long_name="Edge-A", role="CLIENT")
    with db.connection() as conn:
        conn.execute(
            "UPDATE devices SET last_seen = datetime('now'),"
            " mesh_status = 'reachable' WHERE node_id = ?",
            (node_id,),
        )


# ── Constructor ──────────────────────────────────────────────────────


class TestConstructor:
    def test_defaults(self, db: MeshDatabase) -> None:
        mgr = SyncRelayManager(db=db)
        assert mgr.cooldown_minutes == 10
        assert mgr._production_url == ""
        assert mgr._bridge is None

    def test_custom_params(self, db: MeshDatabase) -> None:
        mgr = SyncRelayManager(
            db=db,
            production_url="https://example.com/",
            cooldown_minutes=5,
        )
        assert mgr._production_url == "https://example.com"  # trailing slash stripped
        assert mgr.cooldown_minutes == 5

    def test_fragmenter_and_reassembler_created(self, manager: SyncRelayManager) -> None:
        assert manager._fragmenter is not None
        assert manager._reassembler is not None

    def test_initial_state_empty(self, manager: SyncRelayManager) -> None:
        assert manager._known_sv_hashes == {}
        assert manager._last_sync_by_node == {}
        assert manager._active_sessions == {}


# ── handle_heartbeat_sv_hash ─────────────────────────────────────────


class TestHeartbeatSvHash:
    def test_first_hash_triggers_sync(self, manager: SyncRelayManager, db: MeshDatabase) -> None:
        """First time we see a node's SV hash should trigger sync."""
        _seed_device(db, "!edge1")
        result = manager.handle_heartbeat_sv_hash("!edge1", "abcd1234")
        assert result is True

    def test_same_hash_no_trigger(self, manager: SyncRelayManager) -> None:
        """Repeated identical hash should NOT trigger sync."""
        manager._known_sv_hashes["!edge1"] = "abcd1234"
        result = manager.handle_heartbeat_sv_hash("!edge1", "abcd1234")
        assert result is False

    def test_different_hash_triggers_sync(
        self, manager: SyncRelayManager, db: MeshDatabase
    ) -> None:
        """Changed hash should trigger sync."""
        _seed_device(db, "!edge1")
        manager._known_sv_hashes["!edge1"] = "aaaa0000"
        result = manager.handle_heartbeat_sv_hash("!edge1", "bbbb1111")
        assert result is True

    def test_cooldown_suppresses_trigger(self, manager: SyncRelayManager) -> None:
        """If recent sync completed, suppress new trigger even on mismatch."""
        manager._known_sv_hashes["!edge1"] = "aaaa0000"
        manager._last_sync_by_node["!edge1"] = time.monotonic()  # Just synced
        result = manager.handle_heartbeat_sv_hash("!edge1", "bbbb1111")
        assert result is False

    def test_cooldown_expired_triggers_sync(
        self, manager: SyncRelayManager, db: MeshDatabase
    ) -> None:
        """After cooldown expires, mismatch should trigger again."""
        _seed_device(db, "!edge1")
        manager._known_sv_hashes["!edge1"] = "aaaa0000"
        # Set last sync to 11 minutes ago (cooldown is 10 min)
        manager._last_sync_by_node["!edge1"] = time.monotonic() - 11 * 60
        result = manager.handle_heartbeat_sv_hash("!edge1", "bbbb1111")
        assert result is True

    def test_active_session_suppresses_trigger(self, manager: SyncRelayManager) -> None:
        """If a sending session is already active for this node, suppress."""
        manager._known_sv_hashes["!edge1"] = "aaaa0000"
        manager._active_sessions["sess01"] = {
            "node_id": "!edge1",
            "status": "sending",
        }
        result = manager.handle_heartbeat_sv_hash("!edge1", "bbbb1111")
        assert result is False

    def test_completed_session_does_not_suppress(
        self, manager: SyncRelayManager, db: MeshDatabase
    ) -> None:
        """Completed session should NOT suppress new trigger."""
        _seed_device(db, "!edge1")
        manager._known_sv_hashes["!edge1"] = "aaaa0000"
        manager._active_sessions["sess01"] = {
            "node_id": "!edge1",
            "status": "completed",  # Not sending
        }
        result = manager.handle_heartbeat_sv_hash("!edge1", "bbbb1111")
        assert result is True

    def test_sv_hash_stored(self, manager: SyncRelayManager) -> None:
        """Hash should be stored for future comparison."""
        manager.handle_heartbeat_sv_hash("!edge1", "abcd1234")
        assert manager._known_sv_hashes["!edge1"] == "abcd1234"

    def test_creates_sv_mismatch_alert(self, manager: SyncRelayManager, db: MeshDatabase) -> None:
        """SV mismatch should create a sync_sv_mismatch alert."""
        _seed_device(db, "!edge1")
        manager._known_sv_hashes["!edge1"] = "aaaa0000"
        manager.handle_heartbeat_sv_hash("!edge1", "bbbb1111")
        alerts = db.get_active_alerts("!edge1")
        types = [a["alert_type"] for a in alerts]
        assert "sync_sv_mismatch" in types


# ── handle_mesh_text routing ─────────────────────────────────────────


class TestHandleMeshText:
    def test_non_sync_prefix_not_consumed(self, manager: SyncRelayManager) -> None:
        result = manager.handle_mesh_text("HEARTBEAT|!a|100|gps,radio|85|1234567890")
        assert result is False

    def test_sync_sv_consumed(self, manager: SyncRelayManager) -> None:
        sv = json.dumps({"production": 100})
        text = format_sync_sv("!edge1", sv)
        with patch.object(manager, "_handle_sync_sv") as mock:
            result = manager.handle_mesh_text(text, "!edge1")
        assert result is True
        mock.assert_called_once()

    def test_sync_frag_consumed(self, manager: SyncRelayManager) -> None:
        data = b"hello"
        b64 = base64.b64encode(data).decode()
        crc = compute_crc16(data)
        text = format_sync_frag("abc123", 0, 1, crc, b64)
        with patch.object(manager, "_handle_incoming_fragment") as mock:
            result = manager.handle_mesh_text(text, "!edge1")
        assert result is True
        mock.assert_called_once()

    def test_sync_ack_consumed(self, manager: SyncRelayManager) -> None:
        text = format_sync_ack("abc123", 0)
        with patch.object(manager, "_handle_sync_ack") as mock:
            result = manager.handle_mesh_text(text, "!edge1")
        assert result is True
        mock.assert_called_once_with("abc123", 0)

    def test_sync_nack_consumed(self, manager: SyncRelayManager) -> None:
        text = format_sync_nack("abc123", 2)
        with patch.object(manager, "_handle_sync_nack") as mock:
            result = manager.handle_mesh_text(text, "!edge1")
        assert result is True
        mock.assert_called_once_with("abc123", 2)

    def test_unknown_sync_prefix_consumed(self, manager: SyncRelayManager) -> None:
        """SYNC_ messages with unknown subtypes are consumed (logged and ignored)."""
        result = manager.handle_mesh_text("SYNC_UNKNOWN|data", "!edge1")
        assert result is True

    def test_invalid_sync_sv_no_crash(self, manager: SyncRelayManager) -> None:
        """Malformed SYNC_SV should not crash."""
        result = manager.handle_mesh_text("SYNC_SV|", "!edge1")
        assert result is True  # Still consumed


# ── trigger_sync_for_node ────────────────────────────────────────────


class TestTriggerSync:
    def test_successful_sync_with_delta(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        http_client: MagicMock,
    ) -> None:
        """Full sync cycle: fetch delta → strip → fragment → send."""
        _seed_device(db, "!edge1")
        delta = {
            "state_vector": {"production": 150},
            "conversations": [
                {
                    "id": "msg-1",
                    "timestamp": 100,
                    "node_id": "production",
                    "data": "Hello world — this is big content",
                },
            ],
            "memories": {},
            "config": {},
        }
        http_client.post.return_value = _make_response(200, delta)

        result = manager.trigger_sync_for_node("!edge1", {"production": 100})
        assert result["status"] == "completed"
        assert result["items"] >= 1
        assert "session_id" in result

    def test_sync_empty_delta(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        http_client: MagicMock,
    ) -> None:
        """Delta with no items → completed with 0 items."""
        _seed_device(db, "!edge1")
        delta = {
            "state_vector": {},
            "conversations": [],
            "memories": {},
            "config": {},
        }
        http_client.post.return_value = _make_response(200, delta)

        result = manager.trigger_sync_for_node("!edge1", {})
        assert result["status"] == "completed"
        assert result["items"] == 0

    def test_production_api_error(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        http_client: MagicMock,
    ) -> None:
        """Production API failure → sync fails gracefully."""
        _seed_device(db, "!edge1")
        http_client.post.return_value = _make_response(500)

        result = manager.trigger_sync_for_node("!edge1", {"production": 100})
        assert result["status"] == "failed"
        assert "production_api_error" in result["error"]

    def test_sync_creates_log_entry(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        http_client: MagicMock,
    ) -> None:
        """Sync should create an audit log entry."""
        _seed_device(db, "!edge1")
        delta = {
            "state_vector": {},
            "conversations": [],
            "memories": {},
            "config": {},
        }
        http_client.post.return_value = _make_response(200, delta)

        manager.trigger_sync_for_node("!edge1", {})
        logs = db.get_sync_log_for_node("!edge1")
        assert len(logs) >= 1
        assert logs[0]["direction"] == "to_edge"

    def test_sync_creates_alerts(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        http_client: MagicMock,
    ) -> None:
        """Successful sync with items should create started + completed alerts."""
        _seed_device(db, "!edge1")
        delta = {
            "state_vector": {"production": 200},
            "conversations": [
                {"id": "msg-1", "timestamp": 100, "node_id": "prod"},
            ],
            "memories": {},
            "config": {},
        }
        http_client.post.return_value = _make_response(200, delta)

        manager.trigger_sync_for_node("!edge1", {"production": 50})
        alerts = db.get_active_alerts("!edge1")
        types = [a["alert_type"] for a in alerts]
        assert "sync_relay_started" in types
        assert "sync_relay_completed" in types

    def test_exception_handled_gracefully(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        http_client: MagicMock,
    ) -> None:
        """Unexpected exception → returns failed, doesn't crash."""
        _seed_device(db, "!edge1")
        # Patch _strip_content_for_lora to raise AFTER fetch succeeds,
        # exercising the outer try/except in trigger_sync_for_node.
        http_client.post.return_value = _make_response(200, {"conversations": []})
        with patch.object(manager, "_strip_content_for_lora", side_effect=ValueError("kaboom")):
            result = manager.trigger_sync_for_node("!edge1", {"production": 100})
        assert result["status"] == "failed"
        assert "kaboom" in result["error"]


# ── _strip_content_for_lora ──────────────────────────────────────────


class TestStripContentForLora:
    def test_data_field_removed(self, manager: SyncRelayManager) -> None:
        delta = {
            "conversations": [
                {
                    "id": "msg-1",
                    "timestamp": 100,
                    "node_id": "production",
                    "data": "This is big message content",
                }
            ],
        }
        stripped = manager._strip_content_for_lora(delta)
        assert "data" not in stripped["conversations"][0]
        assert stripped["conversations"][0]["id"] == "msg-1"
        assert stripped["conversations"][0]["timestamp"] == 100

    def test_deletion_fields_preserved(self, manager: SyncRelayManager) -> None:
        delta = {
            "conversations": [
                {
                    "id": "msg-2",
                    "timestamp": 200,
                    "node_id": "production",
                    "deleted": True,
                    "deleted_at": 12345,
                    "deleted_by": "user-1",
                    "data": "old content",
                }
            ],
        }
        stripped = manager._strip_content_for_lora(delta)
        conv = stripped["conversations"][0]
        assert conv["deleted"] is True
        assert conv["deleted_at"] == 12345
        assert conv["deleted_by"] == "user-1"
        assert "data" not in conv

    def test_project_id_preserved(self, manager: SyncRelayManager) -> None:
        delta = {
            "conversations": [
                {
                    "id": "msg-3",
                    "timestamp": 300,
                    "node_id": "production",
                    "project_id": "proj-alpha",
                }
            ],
        }
        stripped = manager._strip_content_for_lora(delta)
        assert stripped["conversations"][0]["project_id"] == "proj-alpha"

    def test_non_conversation_fields_untouched(self, manager: SyncRelayManager) -> None:
        delta = {
            "conversations": [],
            "memories": {"key1": {"value": "val1"}},
            "config": {"setting": "on"},
            "state_vector": {"node1": 50},
        }
        stripped = manager._strip_content_for_lora(delta)
        assert stripped["memories"] == delta["memories"]
        assert stripped["config"] == delta["config"]
        assert stripped["state_vector"] == delta["state_vector"]


# ── _prioritize_and_queue ────────────────────────────────────────────


class TestPrioritizeAndQueue:
    def test_tombstones_queued_as_p1(self, manager: SyncRelayManager, db: MeshDatabase) -> None:
        _seed_device(db, "!edge1")
        delta = {
            "conversations": [
                {"id": "msg-1", "timestamp": 100, "node_id": "prod", "deleted": True},
            ],
            "memories": {},
            "config": {},
        }
        result = manager._prioritize_and_queue("!edge1", "sess01", delta)
        assert result["total_items"] >= 1

        # Verify DB entries exist
        entries = db.get_pending_sync_entries()
        assert len(entries) >= 1

    def test_metadata_queued_as_p2(self, manager: SyncRelayManager, db: MeshDatabase) -> None:
        _seed_device(db, "!edge1")
        delta = {
            "conversations": [
                {"id": "msg-2", "timestamp": 200, "node_id": "prod"},
            ],
            "memories": {},
            "config": {},
        }
        result = manager._prioritize_and_queue("!edge1", "sess02", delta)
        assert result["total_items"] >= 1

    def test_empty_delta_no_fragments(self, manager: SyncRelayManager, db: MeshDatabase) -> None:
        delta = {"conversations": [], "memories": {}, "config": {}}
        result = manager._prioritize_and_queue("!edge1", "sess03", delta)
        assert result["total_items"] == 0
        assert result["total_fragments"] == 0

    def test_fragments_stored_in_db(self, manager: SyncRelayManager, db: MeshDatabase) -> None:
        _seed_device(db, "!edge1")
        delta = {
            "conversations": [
                {"id": f"msg-{i}", "timestamp": i, "node_id": "prod"} for i in range(10)
            ],
            "memories": {},
            "config": {},
        }
        result = manager._prioritize_and_queue("!edge1", "sess04", delta)
        # Each priority bucket gets its own frag session; query by the returned IDs
        frag_sessions = result["frag_sessions"]
        assert len(frag_sessions) >= 1
        total_frags = 0
        for fsid in frag_sessions:
            total_frags += len(db.get_fragments_for_session(fsid))
        assert total_frags >= 1

    def test_multiple_priority_levels(self, manager: SyncRelayManager, db: MeshDatabase) -> None:
        _seed_device(db, "!edge1")
        delta = {
            "conversations": [
                {"id": "tomb-1", "timestamp": 1, "node_id": "prod", "deleted": True},
                {"id": "meta-1", "timestamp": 2, "node_id": "prod"},
            ],
            "memories": {"mem1": {"value": "v1"}},
            "config": {"setting": "yes"},
        }
        result = manager._prioritize_and_queue("!edge1", "sess05", delta)
        # All 4 buckets should produce items
        assert result["total_items"] >= 4


# ── _send_pending_fragments ──────────────────────────────────────────


class TestSendPendingFragments:
    def test_sends_via_bridge(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        bridge: MagicMock,
    ) -> None:
        """Pending fragments should be sent via RadioBridge."""
        _seed_device(db, "!edge1")
        # Insert a fragment
        db.create_sync_fragment(
            session_id="sess01",
            seq=0,
            total=1,
            direction="outbound",
            payload_b64="dGVzdA==",
            crc16="abcd",
        )
        sent = manager._send_pending_fragments("sess01", "!edge1")
        assert sent == 1
        bridge.send_text.assert_called_once()

    def test_no_bridge_returns_zero(self, db: MeshDatabase) -> None:
        mgr = SyncRelayManager(db=db, bridge=None)
        db.create_sync_fragment(
            session_id="sess01",
            seq=0,
            total=1,
            direction="outbound",
            payload_b64="dGVzdA==",
            crc16="abcd",
        )
        sent = mgr._send_pending_fragments("sess01", "!edge1")
        assert sent == 0

    def test_bridge_failure_logged(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        bridge: MagicMock,
    ) -> None:
        """Bridge send failure should not crash."""
        _seed_device(db, "!edge1")
        db.create_sync_fragment(
            session_id="sess01",
            seq=0,
            total=1,
            direction="outbound",
            payload_b64="dGVzdA==",
            crc16="abcd",
        )
        bridge.send_text.side_effect = OSError("radio error")
        sent = manager._send_pending_fragments("sess01", "!edge1")
        assert sent == 0  # Graceful failure


# ── ACK / NACK handling ─────────────────────────────────────────────


class TestAckNack:
    def _setup_active_session(self, manager: SyncRelayManager, db: MeshDatabase) -> str:
        """Create an active session with 2 outbound fragments."""
        sid = "ack_sess"
        manager._active_sessions[sid] = {
            "node_id": "!edge1",
            "log_id": 1,
            "status": "sending",
            "total_fragments": 2,
            "acked": set(),
            "started": time.monotonic(),
        }
        for seq in range(2):
            db.create_sync_fragment(
                session_id=sid,
                seq=seq,
                total=2,
                direction="outbound",
                payload_b64=base64.b64encode(f"data-{seq}".encode()).decode(),
                crc16=compute_crc16(f"data-{seq}".encode()),
            )
        return sid

    def test_ack_marks_fragment(self, manager: SyncRelayManager, db: MeshDatabase) -> None:
        sid = self._setup_active_session(manager, db)
        manager._handle_sync_ack(sid, 0)
        assert 0 in manager._active_sessions[sid]["acked"]

    def test_all_acked_completes_session(self, manager: SyncRelayManager, db: MeshDatabase) -> None:
        """Session should complete when all fragments are ACKed."""
        sid = self._setup_active_session(manager, db)
        manager._handle_sync_ack(sid, 0)
        manager._handle_sync_ack(sid, 1)
        assert manager._active_sessions[sid]["status"] == "completed"

    def test_completed_session_updates_cooldown(
        self, manager: SyncRelayManager, db: MeshDatabase
    ) -> None:
        """Completed session should update last_sync timestamp."""
        sid = self._setup_active_session(manager, db)
        manager._handle_sync_ack(sid, 0)
        manager._handle_sync_ack(sid, 1)
        assert "!edge1" in manager._last_sync_by_node

    def test_ack_for_unknown_session_ignored(self, manager: SyncRelayManager) -> None:
        """ACK for unknown session should not crash."""
        manager._handle_sync_ack("nonexistent", 0)
        # No exception

    def test_nack_triggers_retransmit(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        bridge: MagicMock,
    ) -> None:
        """NACK should trigger retransmission."""
        _seed_device(db, "!edge1")
        sid = self._setup_active_session(manager, db)
        manager._handle_sync_nack(sid, 0)
        # Bridge should have been called for retransmit
        bridge.send_text.assert_called()

    def test_nack_for_unknown_session_ignored(self, manager: SyncRelayManager) -> None:
        manager._handle_sync_nack("nonexistent", 0)
        # No exception


# ── Incoming fragments (edge → gateway) ─────────────────────────────


class TestIncomingFragments:
    def test_single_fragment_relayed_to_production(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        bridge: MagicMock,
        http_client: MagicMock,
    ) -> None:
        """Single fragment that completes immediately should relay to Production."""
        _seed_device(db, "!edge1")
        payload = json.dumps([{"id": "msg-1", "timestamp": 100}])
        data = payload.encode()
        b64 = base64.b64encode(data).decode()
        crc = compute_crc16(data)

        http_client.post.return_value = _make_response(200, {"accepted": 1})

        parsed = {
            "session_id": "push01",
            "seq": 0,
            "total": 1,
            "b64_payload": b64,
            "crc16": crc,
        }
        manager._handle_incoming_fragment(parsed, "!edge1")

        # Should have sent ACK + relayed to Production
        bridge.send_text.assert_called()  # ACK
        http_client.post.assert_called_once()

    def test_incomplete_fragment_sends_ack(
        self,
        manager: SyncRelayManager,
        bridge: MagicMock,
    ) -> None:
        """Fragment that doesn't complete the session should send ACK only."""
        data = b"partial"
        b64 = base64.b64encode(data).decode()
        crc = compute_crc16(data)

        parsed = {
            "session_id": "push02",
            "seq": 0,
            "total": 3,  # 3 total — so 1/3 not complete
            "b64_payload": b64,
            "crc16": crc,
        }
        manager._handle_incoming_fragment(parsed, "!edge1")
        bridge.send_text.assert_called_once()  # Just ACK

    def test_crc_mismatch_sends_nack(
        self,
        manager: SyncRelayManager,
        bridge: MagicMock,
    ) -> None:
        """Bad CRC should send NACK."""
        data = b"hello"
        b64 = base64.b64encode(data).decode()

        parsed = {
            "session_id": "push03",
            "seq": 0,
            "total": 1,
            "b64_payload": b64,
            "crc16": "0000",  # Wrong CRC
        }
        manager._handle_incoming_fragment(parsed, "!edge1")
        # Should have called send_text with a NACK
        call_args = bridge.send_text.call_args
        assert call_args is not None
        wire = call_args[0][0]
        assert "SYNC_NACK" in wire


# ── Edge push relay ─────────────────────────────────────────────────


class TestEdgePushRelay:
    def test_relay_success(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        http_client: MagicMock,
    ) -> None:
        """Successful relay should update sync log."""
        _seed_device(db, "!edge1")
        http_client.post.return_value = _make_response(200, {"accepted": 3})

        payload = json.dumps([{"id": f"msg-{i}"} for i in range(3)])
        manager._relay_edge_push_to_production("!edge1", "push_sess", payload)

        logs = db.get_sync_log_for_node("!edge1")
        assert len(logs) >= 1
        assert logs[0]["direction"] == "from_edge"

    def test_relay_no_production_url(self, db: MeshDatabase) -> None:
        """No production URL → relay silently skipped."""
        mgr = SyncRelayManager(db=db)
        # Should not crash
        mgr._relay_edge_push_to_production("!edge1", "push_sess", "[]")

    def test_relay_api_failure(
        self,
        manager: SyncRelayManager,
        db: MeshDatabase,
        http_client: MagicMock,
    ) -> None:
        """API failure → sync log marked as failed."""
        _seed_device(db, "!edge1")
        http_client.post.return_value = _make_response(500)

        manager._relay_edge_push_to_production("!edge1", "push_sess", "[]")
        logs = db.get_sync_log_for_node("!edge1")
        assert len(logs) >= 1


# ── _fetch_delta_from_production ─────────────────────────────────────


class TestFetchDelta:
    def test_no_production_url_returns_none(self, db: MeshDatabase) -> None:
        mgr = SyncRelayManager(db=db)
        assert mgr._fetch_delta_from_production("!edge1", {}) is None

    def test_no_http_client_returns_none(self, db: MeshDatabase) -> None:
        mgr = SyncRelayManager(db=db, production_url="https://example.com")
        assert mgr._fetch_delta_from_production("!edge1", {}) is None

    def test_success_returns_delta(self, manager: SyncRelayManager, http_client: MagicMock) -> None:
        delta = {"conversations": [], "state_vector": {}}
        http_client.post.return_value = _make_response(200, delta)
        result = manager._fetch_delta_from_production("!edge1", {})
        assert result == delta

    def test_api_500_returns_none(self, manager: SyncRelayManager, http_client: MagicMock) -> None:
        http_client.post.return_value = _make_response(500)
        assert manager._fetch_delta_from_production("!edge1", {}) is None

    def test_connection_error_returns_none(
        self, manager: SyncRelayManager, http_client: MagicMock
    ) -> None:
        http_client.post.side_effect = ConnectionError("DNS fail")
        assert manager._fetch_delta_from_production("!edge1", {}) is None

    def test_correct_url_and_headers(
        self, manager: SyncRelayManager, http_client: MagicMock
    ) -> None:
        """Verify the request URL, payload, and auth headers."""
        http_client.post.return_value = _make_response(200, {})
        manager._fetch_delta_from_production("!edge1", {"prod": 100})

        call_args = http_client.post.call_args
        assert call_args[0][0] == "https://jenn2u.ai/api/v1/sync"
        assert call_args[1]["json"]["node_id"] == "!edge1"
        assert call_args[1]["json"]["state_vector"] == {"prod": 100}
        assert call_args[1]["headers"]["X-Device-Token"] == "test-token-123"


# ── get_sync_status ──────────────────────────────────────────────────


class TestGetSyncStatus:
    def test_initial_status(self, manager: SyncRelayManager) -> None:
        status = manager.get_sync_status()
        assert status["active_sessions"] == 0
        assert status["known_nodes"] == 0
        assert status["cooldown_minutes"] == 10
        assert "pending_queue_entries" in status
        assert "reassembler_sessions" in status

    def test_status_with_active_session(self, manager: SyncRelayManager) -> None:
        manager._active_sessions["sess01"] = {"status": "sending"}
        manager._known_sv_hashes["!edge1"] = "abcd1234"
        status = manager.get_sync_status()
        assert status["active_sessions"] == 1
        assert status["known_nodes"] == 1

    def test_completed_sessions_not_counted_as_active(self, manager: SyncRelayManager) -> None:
        manager._active_sessions["sess01"] = {"status": "completed"}
        status = manager.get_sync_status()
        assert status["active_sessions"] == 0


# ── get_node_sync_history ────────────────────────────────────────────


class TestGetNodeSyncHistory:
    def test_empty_history(self, manager: SyncRelayManager) -> None:
        history = manager.get_node_sync_history("!edge1")
        assert history == []

    def test_history_from_db(self, manager: SyncRelayManager, db: MeshDatabase) -> None:
        _seed_device(db, "!edge1")
        db.create_sync_log("!edge1", "to_edge", session_id="sess01")
        history = manager.get_node_sync_history("!edge1")
        assert len(history) == 1


# ── Schema v11 table tests ──────────────────────────────────────────


class TestSchemaV11:
    def test_schema_version_is_11(self, db: MeshDatabase) -> None:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
        assert row["version"] == 11

    def test_sync_queue_table_exists(self, db: MeshDatabase) -> None:
        entry_id = db.create_sync_queue_entry(
            node_id="!a",
            session_id="sess01",
            direction="to_edge",
            payload_json="{}",
        )
        assert entry_id > 0

    def test_sync_fragments_table_exists(self, db: MeshDatabase) -> None:
        frag_id = db.create_sync_fragment(
            session_id="sess01",
            seq=0,
            total=1,
            direction="outbound",
            payload_b64="dGVzdA==",
            crc16="abcd",
        )
        assert frag_id > 0

    def test_sync_log_table_exists(self, db: MeshDatabase) -> None:
        log_id = db.create_sync_log("!a", "to_edge")
        assert log_id > 0
