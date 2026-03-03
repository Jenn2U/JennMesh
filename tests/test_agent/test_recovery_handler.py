"""Tests for RecoveryHandler — target-agent-side command execution."""

import time
from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.agent.recovery_handler import (
    COMMAND_TIMEOUT_SECONDS,
    MAX_NONCE_HISTORY,
    RecoveryHandler,
)
from jenn_mesh.models.recovery import (
    ADMIN_CHANNEL_INDEX,
    MAX_COMMAND_AGE_SECONDS,
    format_recovery_text,
)


@pytest.fixture
def bridge() -> MagicMock:
    """Mock RadioBridge with send_text method."""
    mock = MagicMock()
    mock.send_text.return_value = True
    return mock


@pytest.fixture
def handler(bridge: MagicMock) -> RecoveryHandler:
    """Create a RecoveryHandler with a mock bridge."""
    return RecoveryHandler(bridge=bridge, node_id="!target01")


def _make_command(
    cmd_id: int = 42,
    command_type: str = "system_status",
    args: str = "",
    nonce: str = "abcd1234",
    timestamp: int | None = None,
) -> str:
    """Helper to build a well-formed recovery command text."""
    if timestamp is None:
        timestamp = int(time.time())
    return format_recovery_text(cmd_id, command_type, args, nonce, timestamp)


class TestHandleMeshText:
    """Tests for the main handler entry point."""

    def test_ignores_non_recovery_text(self, handler: RecoveryHandler) -> None:
        assert handler.handle_mesh_text("HEARTBEAT|node|3600|ok|85|ts") is False

    def test_ignores_malformed_recovery(self, handler: RecoveryHandler) -> None:
        assert handler.handle_mesh_text("RECOVER|bad") is False

    @patch("jenn_mesh.agent.recovery_handler.RecoveryHandler._execute_command")
    def test_processes_valid_recovery_command(
        self, mock_exec: MagicMock, handler: RecoveryHandler, bridge: MagicMock
    ) -> None:
        mock_exec.return_value = (True, "ok")
        text = _make_command(cmd_id=1, command_type="system_status", nonce="aa112233")
        result = handler.handle_mesh_text(text, from_id="!sender01")
        assert result is True
        mock_exec.assert_called_once_with("system_status", "")
        # ACK should be sent
        bridge.send_text.assert_called_once()
        ack_text = bridge.send_text.call_args[0][0]
        assert ack_text.startswith("RECOVER_ACK|1|success|")

    @patch("jenn_mesh.agent.recovery_handler.RecoveryHandler._execute_command")
    def test_ack_sent_on_failure(
        self, mock_exec: MagicMock, handler: RecoveryHandler, bridge: MagicMock
    ) -> None:
        mock_exec.return_value = (False, "something broke")
        text = _make_command(cmd_id=5, nonce="bb223344")
        handler.handle_mesh_text(text)
        ack_text = bridge.send_text.call_args[0][0]
        assert "RECOVER_ACK|5|failed|" in ack_text

    def test_rejects_unknown_command_type(
        self, handler: RecoveryHandler, bridge: MagicMock
    ) -> None:
        # Manually craft text with invalid command type
        text = f"RECOVER|1|format_disk||aabb1122|{int(time.time())}"
        result = handler.handle_mesh_text(text)
        assert result is True
        ack_text = bridge.send_text.call_args[0][0]
        assert "unknown command" in ack_text


class TestValidation:
    """Tests for nonce and timestamp validation."""

    @patch("jenn_mesh.agent.recovery_handler.RecoveryHandler._execute_command")
    def test_rejects_duplicate_nonce(
        self, mock_exec: MagicMock, handler: RecoveryHandler, bridge: MagicMock
    ) -> None:
        mock_exec.return_value = (True, "ok")
        text1 = _make_command(cmd_id=1, nonce="same_nonce")
        text2 = _make_command(cmd_id=2, nonce="same_nonce")

        handler.handle_mesh_text(text1)
        handler.handle_mesh_text(text2)

        # First should execute, second should be rejected
        mock_exec.assert_called_once()
        # Second call should ACK failure
        last_ack = bridge.send_text.call_args_list[-1][0][0]
        assert "duplicate nonce" in last_ack

    @patch("jenn_mesh.agent.recovery_handler.RecoveryHandler._execute_command")
    def test_rejects_stale_timestamp(
        self, mock_exec: MagicMock, handler: RecoveryHandler, bridge: MagicMock
    ) -> None:
        old_ts = int(time.time()) - MAX_COMMAND_AGE_SECONDS - 60
        text = _make_command(cmd_id=1, nonce="fresh123", timestamp=old_ts)
        handler.handle_mesh_text(text)

        mock_exec.assert_not_called()
        ack_text = bridge.send_text.call_args[0][0]
        assert "command too old" in ack_text

    @patch("jenn_mesh.agent.recovery_handler.RecoveryHandler._execute_command")
    def test_accepts_recent_timestamp(self, mock_exec: MagicMock, handler: RecoveryHandler) -> None:
        mock_exec.return_value = (True, "ok")
        text = _make_command(cmd_id=1, nonce="recent11", timestamp=int(time.time()) - 10)
        handler.handle_mesh_text(text)
        mock_exec.assert_called_once()

    def test_nonce_deque_bounded(self, handler: RecoveryHandler) -> None:
        """Verify nonce history doesn't grow unbounded."""
        for i in range(MAX_NONCE_HISTORY + 50):
            handler._seen_nonces.append(f"nonce_{i:04d}")
        assert len(handler._seen_nonces) == MAX_NONCE_HISTORY


class TestExecuteReboot:
    """Tests for reboot execution."""

    @patch("jenn_mesh.agent.recovery_handler.subprocess.Popen")
    def test_reboot_success(self, mock_popen: MagicMock, handler: RecoveryHandler) -> None:
        success, message = handler._execute_reboot()
        assert success is True
        assert "reboot initiated" in message
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert cmd == ["sudo", "shutdown", "-r", "now"]

    @patch("jenn_mesh.agent.recovery_handler.subprocess.Popen")
    def test_reboot_failure(self, mock_popen: MagicMock, handler: RecoveryHandler) -> None:
        mock_popen.side_effect = PermissionError("not allowed")
        success, message = handler._execute_reboot()
        assert success is False
        assert "reboot failed" in message


class TestExecuteRestartService:
    """Tests for service restart execution."""

    @patch("jenn_mesh.agent.recovery_handler.subprocess.run")
    def test_restart_allowed_service(self, mock_run: MagicMock, handler: RecoveryHandler) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        success, message = handler._execute_restart_service("jennedge")
        assert success is True
        assert "jennedge restarted" in message
        mock_run.assert_called_once_with(
            ["sudo", "systemctl", "restart", "jennedge"],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )

    def test_rejects_disallowed_service(self, handler: RecoveryHandler) -> None:
        success, message = handler._execute_restart_service("nginx")
        assert success is False
        assert "not allowed" in message

    @patch("jenn_mesh.agent.recovery_handler.subprocess.run")
    def test_restart_service_failure(self, mock_run: MagicMock, handler: RecoveryHandler) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="Unit not found")
        success, message = handler._execute_restart_service("jennedge")
        assert success is False
        assert "restart failed" in message

    @patch("jenn_mesh.agent.recovery_handler.subprocess.run")
    def test_restart_service_timeout(self, mock_run: MagicMock, handler: RecoveryHandler) -> None:
        import subprocess as sp

        mock_run.side_effect = sp.TimeoutExpired(cmd="restart", timeout=30)
        success, message = handler._execute_restart_service("jennedge")
        assert success is False
        assert "timed out" in message


class TestExecuteSystemStatus:
    """Tests for system status diagnostics collection."""

    @patch("jenn_mesh.agent.recovery_handler.subprocess.run")
    def test_system_status_collects_diagnostics(
        self, mock_run: MagicMock, handler: RecoveryHandler
    ) -> None:
        # Mock successful subprocess calls
        def side_effect(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if cmd[0] == "uptime":
                result.stdout = "up 3 days, 2 hours"
            elif cmd[0] == "df":
                result.stdout = "Use%\n 42%"
            elif cmd[0] == "free":
                result.stdout = (
                    "              total   used   free\nMem:           3840   1024   2816"
                )
            elif cmd[0] == "systemctl":
                result.stdout = "active"
            return result

        mock_run.side_effect = side_effect

        success, message = handler._execute_system_status()
        assert success is True
        assert "up:" in message
        assert "disk:" in message
        assert "mem:" in message

    @patch("jenn_mesh.agent.recovery_handler.subprocess.run")
    def test_system_status_handles_failures_gracefully(
        self, mock_run: MagicMock, handler: RecoveryHandler
    ) -> None:
        """If subprocess calls fail, status should still return with '?' placeholders."""
        mock_run.side_effect = Exception("command not found")
        success, message = handler._execute_system_status()
        assert success is True
        assert "?" in message  # Fallback placeholders

    @patch("jenn_mesh.agent.recovery_handler.subprocess.run")
    def test_system_status_fits_lora_limit(
        self, mock_run: MagicMock, handler: RecoveryHandler
    ) -> None:
        """Status message must stay under 180 chars for LoRa ACK budget."""

        def side_effect(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = "x" * 200  # Overly long output
            return result

        mock_run.side_effect = side_effect

        success, message = handler._execute_system_status()
        assert success is True
        assert len(message) <= 180


class TestSendAck:
    """Tests for ACK sending over mesh."""

    def test_ack_sent_on_channel_1(self, handler: RecoveryHandler, bridge: MagicMock) -> None:
        handler._send_ack(42, True, "rebooting")
        bridge.send_text.assert_called_once()
        kwargs = bridge.send_text.call_args[1]
        assert kwargs["channel_index"] == ADMIN_CHANNEL_INDEX

    def test_ack_broadcast_destination(self, handler: RecoveryHandler, bridge: MagicMock) -> None:
        handler._send_ack(42, True, "ok")
        kwargs = bridge.send_text.call_args[1]
        assert kwargs["destination"] is None  # Broadcast

    def test_success_ack_format(self, handler: RecoveryHandler, bridge: MagicMock) -> None:
        handler._send_ack(42, True, "jennedge restarted")
        ack_text = bridge.send_text.call_args[0][0]
        assert ack_text == "RECOVER_ACK|42|success|jennedge restarted"

    def test_failed_ack_format(self, handler: RecoveryHandler, bridge: MagicMock) -> None:
        handler._send_ack(42, False, "permission denied")
        ack_text = bridge.send_text.call_args[0][0]
        assert ack_text == "RECOVER_ACK|42|failed|permission denied"

    def test_handles_send_failure(self, handler: RecoveryHandler, bridge: MagicMock) -> None:
        """If bridge.send_text fails, handler should not crash."""
        bridge.send_text.return_value = False
        handler._send_ack(42, True, "ok")  # Should not raise
