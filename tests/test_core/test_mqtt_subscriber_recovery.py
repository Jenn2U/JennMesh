"""Tests for MQTT subscriber recovery ACK handling."""

import tempfile
from unittest.mock import MagicMock

import pytest

from jenn_mesh.core.mqtt_subscriber import MQTTSubscriber
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.recovery import format_recovery_ack, generate_nonce


@pytest.fixture
def db() -> MeshDatabase:
    """Create a temporary test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def subscriber(db: MeshDatabase) -> MQTTSubscriber:
    """Create an MQTT subscriber with a test database."""
    return MQTTSubscriber(db=db, broker="localhost", port=1884)


class TestHandleRecoveryAck:
    """Tests for _handle_recovery_ack in MQTT subscriber."""

    def test_success_ack_marks_completed(
        self, subscriber: MQTTSubscriber, db: MeshDatabase
    ) -> None:
        """A RECOVER_ACK with status=success should mark the command completed."""
        cmd_id = db.create_recovery_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            args="",
            nonce=generate_nonce(),
            sender="test",
            expires_at="2030-01-01T00:05:00",
        )
        ack_text = format_recovery_ack(cmd_id, "success", "rebooting now")

        subscriber._handle_text("!a1b2c3d4", {"text": ack_text})

        stored = db.get_recovery_command(cmd_id)
        assert stored["status"] == "completed"
        assert stored["result_message"] == "rebooting now"
        assert stored["completed_at"] is not None

    def test_failed_ack_marks_failed(self, subscriber: MQTTSubscriber, db: MeshDatabase) -> None:
        """A RECOVER_ACK with status=failed should mark the command failed."""
        cmd_id = db.create_recovery_command(
            target_node_id="!a1b2c3d4",
            command_type="restart_service",
            args="jennedge",
            nonce=generate_nonce(),
            sender="test",
            expires_at="2030-01-01T00:05:00",
        )
        ack_text = format_recovery_ack(cmd_id, "failed", "service not found")

        subscriber._handle_text("!a1b2c3d4", {"text": ack_text})

        stored = db.get_recovery_command(cmd_id)
        assert stored["status"] == "failed"
        assert stored["result_message"] == "service not found"

    def test_malformed_ack_ignored(self, subscriber: MQTTSubscriber, db: MeshDatabase) -> None:
        """A malformed RECOVER_ACK should be logged and ignored without errors."""
        # Missing fields — should not crash
        subscriber._handle_text("!a1b2c3d4", {"text": "RECOVER_ACK|bad"})
        # No exception raised, no crash

    def test_callback_invoked_on_success(
        self, subscriber: MQTTSubscriber, db: MeshDatabase
    ) -> None:
        """The on_recovery_ack callback should be called with cmd_id, status, node_id."""
        mock_callback = MagicMock()
        subscriber.set_callbacks(on_recovery_ack=mock_callback)

        cmd_id = db.create_recovery_command(
            target_node_id="!a1b2c3d4",
            command_type="system_status",
            args="",
            nonce=generate_nonce(),
            sender="test",
            expires_at="2030-01-01T00:05:00",
        )
        ack_text = format_recovery_ack(cmd_id, "success", "ok")

        subscriber._handle_text("!a1b2c3d4", {"text": ack_text})

        mock_callback.assert_called_once_with(cmd_id, "success", "!a1b2c3d4")

    def test_callback_invoked_on_failure(
        self, subscriber: MQTTSubscriber, db: MeshDatabase
    ) -> None:
        """The on_recovery_ack callback should fire for failed ACKs too."""
        mock_callback = MagicMock()
        subscriber.set_callbacks(on_recovery_ack=mock_callback)

        cmd_id = db.create_recovery_command(
            target_node_id="!deadbeef",
            command_type="reboot",
            args="",
            nonce=generate_nonce(),
            sender="test",
            expires_at="2030-01-01T00:05:00",
        )
        ack_text = format_recovery_ack(cmd_id, "failed", "permission denied")

        subscriber._handle_text("!deadbeef", {"text": ack_text})

        mock_callback.assert_called_once_with(cmd_id, "failed", "!deadbeef")

    def test_recover_ack_takes_priority_over_other_handlers(
        self, subscriber: MQTTSubscriber
    ) -> None:
        """RECOVER_ACK| should be checked before [EMERGENCY: and HEARTBEAT| prefixes."""
        # The method should route RECOVER_ACK first (no crash, no emergency processing)
        # A RECOVER_ACK text that also somehow contained EMERGENCY wouldn't hit
        # the emergency handler — the RECOVER_ACK prefix wins
        subscriber._handle_text("!a1b2c3d4", {"text": "RECOVER_ACK|999|success|all good"})
        # No crash — malformed cmd_id (999 doesn't exist) is handled gracefully

    def test_nonexistent_command_id_handled_gracefully(self, subscriber: MQTTSubscriber) -> None:
        """ACK for a command_id that doesn't exist in DB should not crash."""
        ack_text = format_recovery_ack(99999, "success", "done")
        # Should not raise — mark_completed on nonexistent ID is a no-op in DB
        subscriber._handle_text("!a1b2c3d4", {"text": ack_text})

    def test_empty_message_in_ack(self, subscriber: MQTTSubscriber, db: MeshDatabase) -> None:
        """ACK with empty message should still update status."""
        cmd_id = db.create_recovery_command(
            target_node_id="!a1b2c3d4",
            command_type="system_status",
            args="",
            nonce=generate_nonce(),
            sender="test",
            expires_at="2030-01-01T00:05:00",
        )
        ack_text = format_recovery_ack(cmd_id, "success", "")

        subscriber._handle_text("!a1b2c3d4", {"text": ack_text})

        stored = db.get_recovery_command(cmd_id)
        assert stored["status"] == "completed"
