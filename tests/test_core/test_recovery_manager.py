"""Tests for RecoveryManager — dashboard-side recovery command orchestration."""

import json
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from jenn_mesh.core.recovery_manager import (
    RECOVERY_COMMAND_TOPIC,
    RecoveryManager,
)
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.recovery import (
    ADMIN_CHANNEL_INDEX,
    RATE_LIMIT_SECONDS,
    RecoveryCommandStatus,
    RecoveryCommandType,
)


@pytest.fixture
def db() -> MeshDatabase:
    """Create a temporary test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def mqtt_client() -> MagicMock:
    """Mock MQTT client."""
    return MagicMock()


@pytest.fixture
def manager(db: MeshDatabase, mqtt_client: MagicMock) -> RecoveryManager:
    """Create a RecoveryManager with a mock MQTT client."""
    return RecoveryManager(db=db, mqtt_client=mqtt_client)


@pytest.fixture
def manager_no_mqtt(db: MeshDatabase) -> RecoveryManager:
    """Create a RecoveryManager without MQTT."""
    return RecoveryManager(db=db, mqtt_client=None)


class TestSendCommand:
    """Tests for creating and sending recovery commands."""

    def test_send_reboot_success(self, manager: RecoveryManager) -> None:
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            confirmed=True,
            sender="operator-1",
        )
        assert cmd.id is not None
        assert cmd.id > 0
        assert cmd.command_type == RecoveryCommandType.REBOOT
        assert cmd.target_node_id == "!a1b2c3d4"
        assert cmd.status == RecoveryCommandStatus.PENDING
        assert cmd.confirmed is True
        assert cmd.sender == "operator-1"
        assert cmd.nonce is not None
        assert len(cmd.nonce) == 8

    def test_send_restart_service_success(self, manager: RecoveryManager) -> None:
        cmd = manager.send_command(
            target_node_id="!deadbeef",
            command_type="restart_service",
            args="jennedge",
            confirmed=True,
        )
        assert cmd.command_type == RecoveryCommandType.RESTART_SERVICE
        assert cmd.args == "jennedge"

    def test_send_system_status_success(self, manager: RecoveryManager) -> None:
        cmd = manager.send_command(
            target_node_id="!deadbeef",
            command_type="system_status",
            confirmed=True,
        )
        assert cmd.command_type == RecoveryCommandType.SYSTEM_STATUS
        assert cmd.args == ""

    def test_requires_confirmation(self, manager: RecoveryManager) -> None:
        with pytest.raises(ValueError, match="explicit confirmation"):
            manager.send_command(
                target_node_id="!a1b2c3d4",
                command_type="reboot",
                confirmed=False,
            )

    def test_invalid_command_type(self, manager: RecoveryManager) -> None:
        with pytest.raises(ValueError, match="Invalid command type"):
            manager.send_command(
                target_node_id="!a1b2c3d4",
                command_type="format_disk",
                confirmed=True,
            )

    def test_invalid_service_name(self, manager: RecoveryManager) -> None:
        with pytest.raises(ValueError, match="Invalid service"):
            manager.send_command(
                target_node_id="!a1b2c3d4",
                command_type="restart_service",
                args="nginx",
                confirmed=True,
            )

    def test_invalid_node_id_format(self, manager: RecoveryManager) -> None:
        with pytest.raises(ValueError, match="Invalid target_node_id"):
            manager.send_command(
                target_node_id="bad_id",
                command_type="reboot",
                confirmed=True,
            )

    def test_stores_in_db(self, manager: RecoveryManager, db: MeshDatabase) -> None:
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="system_status",
            confirmed=True,
            sender="tester",
        )
        stored = db.get_recovery_command(cmd.id)
        assert stored is not None
        assert stored["target_node_id"] == "!a1b2c3d4"
        assert stored["command_type"] == "system_status"
        assert stored["sender"] == "tester"
        assert stored["status"] == "pending"
        assert stored["nonce"] == cmd.nonce

    def test_default_sender_is_dashboard(self, manager: RecoveryManager) -> None:
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            confirmed=True,
        )
        assert cmd.sender == "dashboard"

    def test_expires_at_populated(self, manager: RecoveryManager, db: MeshDatabase) -> None:
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            confirmed=True,
        )
        stored = db.get_recovery_command(cmd.id)
        assert stored["expires_at"] is not None
        assert stored["expires_at"] != ""


class TestMQTTPublish:
    """Tests for MQTT command publishing."""

    def test_publishes_to_command_topic(
        self, manager: RecoveryManager, mqtt_client: MagicMock
    ) -> None:
        manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            confirmed=True,
        )
        mqtt_client.publish.assert_called_once()
        topic = mqtt_client.publish.call_args[0][0]
        assert topic == RECOVERY_COMMAND_TOPIC

    def test_command_payload_structure(
        self, manager: RecoveryManager, mqtt_client: MagicMock
    ) -> None:
        manager.send_command(
            target_node_id="!deadbeef",
            command_type="restart_service",
            args="jennedge",
            confirmed=True,
        )
        payload_str = mqtt_client.publish.call_args[0][1]
        payload = json.loads(payload_str)
        assert "command_id" in payload
        assert payload["target_node_id"] == "!deadbeef"
        assert payload["command_type"] == "restart_service"
        assert payload["args"] == "jennedge"
        assert payload["nonce"] is not None
        assert payload["channel_index"] == ADMIN_CHANNEL_INDEX
        assert payload["mesh_text"].startswith("RECOVER|")

    def test_no_mqtt_stores_but_does_not_send(
        self, manager_no_mqtt: RecoveryManager, db: MeshDatabase
    ) -> None:
        cmd = manager_no_mqtt.send_command(
            target_node_id="!a1b2c3d4",
            command_type="system_status",
            confirmed=True,
        )
        stored = db.get_recovery_command(cmd.id)
        assert stored is not None
        assert stored["status"] == "pending"

    def test_mqtt_failure_marks_command_failed(
        self, manager: RecoveryManager, mqtt_client: MagicMock, db: MeshDatabase
    ) -> None:
        mqtt_client.publish.side_effect = Exception("Connection refused")
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            confirmed=True,
        )
        stored = db.get_recovery_command(cmd.id)
        assert stored["status"] == "failed"
        assert "Connection refused" in stored["result_message"]


class TestRateLimit:
    """Tests for per-node rate limiting."""

    def test_rate_limit_blocks_rapid_commands(self, manager: RecoveryManager) -> None:
        manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="system_status",
            confirmed=True,
        )
        with pytest.raises(RuntimeError, match="Rate limited"):
            manager.send_command(
                target_node_id="!a1b2c3d4",
                command_type="system_status",
                confirmed=True,
            )

    def test_rate_limit_allows_different_nodes(self, manager: RecoveryManager) -> None:
        manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="system_status",
            confirmed=True,
        )
        # Should NOT be rate limited — different node
        cmd = manager.send_command(
            target_node_id="!deadbeef",
            command_type="system_status",
            confirmed=True,
        )
        assert cmd.id is not None

    def test_rate_limit_allows_after_cooldown(
        self, db: MeshDatabase, mqtt_client: MagicMock
    ) -> None:
        """Verify rate limit passes when the last command is old enough."""
        # Manually insert a command with an old timestamp
        old_time = (
            datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=RATE_LIMIT_SECONDS + 5)
        ).isoformat()
        with db.connection() as conn:
            conn.execute(
                """INSERT INTO recovery_commands
                   (target_node_id, command_type, args, nonce, sender, status,
                    confirmed, expires_at, created_at)
                   VALUES (?, ?, ?, ?, ?, 'completed', 1, ?, ?)""",
                ("!a1b2c3d4", "reboot", "", "oldnonce", "test", old_time, old_time),
            )

        manager = RecoveryManager(db=db, mqtt_client=mqtt_client)
        # Should NOT be rate limited — old command is beyond cooldown
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="system_status",
            confirmed=True,
        )
        assert cmd.id is not None


class TestStatusTransitions:
    """Tests for command status lifecycle transitions."""

    def test_mark_sent(self, manager: RecoveryManager, db: MeshDatabase) -> None:
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            confirmed=True,
        )
        manager.mark_sent(cmd.id)
        stored = db.get_recovery_command(cmd.id)
        assert stored["status"] == "sent"
        assert stored["sent_at"] is not None

    def test_mark_completed(self, manager: RecoveryManager, db: MeshDatabase) -> None:
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            confirmed=True,
        )
        manager.mark_completed(cmd.id, result_message="rebooting now")
        stored = db.get_recovery_command(cmd.id)
        assert stored["status"] == "completed"
        assert stored["result_message"] == "rebooting now"
        assert stored["completed_at"] is not None

    def test_mark_failed(self, manager: RecoveryManager, db: MeshDatabase) -> None:
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="restart_service",
            args="jennedge",
            confirmed=True,
        )
        manager.mark_failed(cmd.id, error="service not found")
        stored = db.get_recovery_command(cmd.id)
        assert stored["status"] == "failed"
        assert stored["result_message"] == "service not found"
        assert stored["completed_at"] is not None


class TestExpireStaleCommands:
    """Tests for expiry of stale commands."""

    def test_expires_old_pending_commands(self, db: MeshDatabase) -> None:
        """Insert a command with an already-past expires_at — should be expired."""
        past_expiry = (datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)).isoformat()
        db.create_recovery_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            args="",
            nonce="stale123",
            sender="test",
            expires_at=past_expiry,
        )

        manager = RecoveryManager(db=db)
        expired_count = manager.expire_stale_commands()
        assert expired_count == 1

        cmds = db.list_recovery_commands()
        assert cmds[0]["status"] == "expired"

    def test_does_not_expire_future_commands(
        self, manager: RecoveryManager, db: MeshDatabase
    ) -> None:
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="system_status",
            confirmed=True,
        )
        expired_count = manager.expire_stale_commands()
        assert expired_count == 0
        stored = db.get_recovery_command(cmd.id)
        assert stored["status"] == "pending"

    def test_does_not_expire_completed_commands(self, db: MeshDatabase) -> None:
        """Completed commands should not be re-expired even if past expires_at."""
        past_expiry = (datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)).isoformat()
        cmd_id = db.create_recovery_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            args="",
            nonce="done1234",
            sender="test",
            expires_at=past_expiry,
        )
        db.update_recovery_status(cmd_id, "completed", result_message="ok")

        manager = RecoveryManager(db=db)
        expired_count = manager.expire_stale_commands()
        assert expired_count == 0


class TestCommandQueries:
    """Tests for listing and querying commands."""

    def test_get_command(self, manager: RecoveryManager) -> None:
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            confirmed=True,
        )
        stored = manager.get_command(cmd.id)
        assert stored is not None
        assert stored["command_type"] == "reboot"

    def test_get_command_not_found(self, manager: RecoveryManager) -> None:
        assert manager.get_command(9999) is None

    def test_list_commands(self, manager: RecoveryManager) -> None:
        manager.send_command(
            target_node_id="!node1111",
            command_type="system_status",
            confirmed=True,
        )
        # Use a different node to avoid rate limit
        manager.send_command(
            target_node_id="!node2222",
            command_type="reboot",
            confirmed=True,
        )
        cmds = manager.list_commands()
        assert len(cmds) == 2

    def test_list_commands_by_node(self, manager: RecoveryManager) -> None:
        manager.send_command(
            target_node_id="!node1111",
            command_type="system_status",
            confirmed=True,
        )
        manager.send_command(
            target_node_id="!node2222",
            command_type="reboot",
            confirmed=True,
        )
        cmds = manager.list_commands(target_node_id="!node1111")
        assert len(cmds) == 1
        assert cmds[0]["target_node_id"] == "!node1111"


class TestNodeRecoveryStatus:
    """Tests for per-node recovery status summary."""

    def test_empty_status(self, manager: RecoveryManager) -> None:
        status = manager.get_node_recovery_status("!unknown")
        assert status["node_id"] == "!unknown"
        assert status["total_commands"] == 0
        assert status["pending_commands"] == 0
        assert status["last_command_time"] is None
        assert status["last_command_status"] is None

    def test_status_with_pending_command(self, manager: RecoveryManager) -> None:
        manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            confirmed=True,
        )
        status = manager.get_node_recovery_status("!a1b2c3d4")
        assert status["total_commands"] == 1
        assert status["pending_commands"] == 1
        assert status["last_command_status"] == "pending"
        assert status["last_command_time"] is not None

    def test_status_completed_not_pending(self, manager: RecoveryManager) -> None:
        cmd = manager.send_command(
            target_node_id="!a1b2c3d4",
            command_type="system_status",
            confirmed=True,
        )
        manager.mark_completed(cmd.id, result_message="ok")
        status = manager.get_node_recovery_status("!a1b2c3d4")
        assert status["total_commands"] == 1
        assert status["pending_commands"] == 0
        assert status["last_command_status"] == "completed"
