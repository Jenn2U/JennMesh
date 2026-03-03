"""Tests for recovery command models, wire format, and validation."""

import time

import pytest

from jenn_mesh.models.recovery import (
    ADMIN_CHANNEL_INDEX,
    ALLOWED_COMMANDS,
    ALLOWED_SERVICES,
    MAX_COMMAND_AGE_SECONDS,
    NONCE_LENGTH,
    RATE_LIMIT_SECONDS,
    RecoveryCommand,
    RecoveryCommandStatus,
    RecoveryCommandType,
    format_recovery_ack,
    format_recovery_text,
    generate_nonce,
    parse_recovery_ack,
    parse_recovery_text,
)


class TestRecoveryCommandType:
    """RecoveryCommandType enum tests."""

    def test_all_values(self):
        assert RecoveryCommandType.REBOOT == "reboot"
        assert RecoveryCommandType.RESTART_SERVICE == "restart_service"
        assert RecoveryCommandType.RESTART_OLLAMA == "restart_ollama"
        assert RecoveryCommandType.SYSTEM_STATUS == "system_status"

    def test_from_string(self):
        assert RecoveryCommandType("reboot") == RecoveryCommandType.REBOOT
        assert RecoveryCommandType("system_status") == RecoveryCommandType.SYSTEM_STATUS

    def test_invalid_value(self):
        with pytest.raises(ValueError):
            RecoveryCommandType("arbitrary_command")


class TestRecoveryCommandStatus:
    """RecoveryCommandStatus enum tests."""

    def test_all_statuses(self):
        statuses = {s.value for s in RecoveryCommandStatus}
        assert statuses == {"pending", "sending", "sent", "completed", "failed", "expired"}


class TestRecoveryCommand:
    """RecoveryCommand Pydantic model tests."""

    def test_minimal_valid(self):
        cmd = RecoveryCommand(
            target_node_id="!a1b2c3d4",
            command_type=RecoveryCommandType.REBOOT,
            nonce="abcd1234",
        )
        assert cmd.target_node_id == "!a1b2c3d4"
        assert cmd.command_type == RecoveryCommandType.REBOOT
        assert cmd.status == RecoveryCommandStatus.PENDING
        assert cmd.confirmed is False
        assert cmd.sender == "dashboard"
        assert cmd.args == ""

    def test_full_command(self):
        cmd = RecoveryCommand(
            id=42,
            target_node_id="!deadbeef",
            command_type=RecoveryCommandType.RESTART_SERVICE,
            args="jennedge",
            nonce="beef1234",
            status=RecoveryCommandStatus.COMPLETED,
            confirmed=True,
            sender="operator-1",
            result_message="jennedge restarted",
            created_at="2024-03-02T12:00:00",
            sent_at="2024-03-02T12:00:01",
            completed_at="2024-03-02T12:00:05",
            expires_at="2024-03-02T12:05:00",
        )
        assert cmd.id == 42
        assert cmd.result_message == "jennedge restarted"


class TestGenerateNonce:
    """Nonce generation tests."""

    def test_length(self):
        nonce = generate_nonce()
        assert len(nonce) == NONCE_LENGTH

    def test_hex_chars(self):
        nonce = generate_nonce()
        assert all(c in "0123456789abcdef" for c in nonce)

    def test_uniqueness(self):
        nonces = {generate_nonce() for _ in range(100)}
        assert len(nonces) == 100  # All unique


class TestFormatRecoveryText:
    """Wire format encoding tests."""

    def test_basic_format(self):
        text = format_recovery_text(42, "reboot", "", "a1b2c3d4", 1709395200)
        assert text == "RECOVER|42|reboot||a1b2c3d4|1709395200"

    def test_with_args(self):
        text = format_recovery_text(99, "restart_service", "jennedge", "beef1234", 1709395200)
        assert text == "RECOVER|99|restart_service|jennedge|beef1234|1709395200"

    def test_auto_timestamp(self):
        text = format_recovery_text(1, "system_status", "", "abcd1234")
        parts = text.split("|")
        ts = int(parts[5])
        assert abs(ts - int(time.time())) < 5  # Within 5 seconds

    def test_fits_lora_limit(self):
        """Even with long args, the message should be under 256 bytes."""
        text = format_recovery_text(99999, "restart_service", "jennedge", "abcd1234", 1709395200)
        assert len(text.encode("utf-8")) < 256


class TestParseRecoveryText:
    """Wire format decoding tests."""

    def test_round_trip(self):
        original = format_recovery_text(42, "restart_service", "jennedge", "a1b2c3d4", 1709395200)
        parsed = parse_recovery_text(original)
        assert parsed is not None
        assert parsed["cmd_id"] == 42
        assert parsed["command_type"] == "restart_service"
        assert parsed["args"] == "jennedge"
        assert parsed["nonce"] == "a1b2c3d4"
        assert parsed["timestamp"] == 1709395200

    def test_empty_args(self):
        text = "RECOVER|1|reboot||abcd1234|1709395200"
        parsed = parse_recovery_text(text)
        assert parsed is not None
        assert parsed["args"] == ""

    def test_wrong_prefix(self):
        assert parse_recovery_text("HEARTBEAT|abc|123") is None

    def test_too_few_fields(self):
        assert parse_recovery_text("RECOVER|42|reboot") is None

    def test_too_many_fields(self):
        assert parse_recovery_text("RECOVER|42|reboot||abc|123|extra") is None

    def test_non_integer_cmd_id(self):
        assert parse_recovery_text("RECOVER|abc|reboot||nonce|123") is None

    def test_non_integer_timestamp(self):
        assert parse_recovery_text("RECOVER|42|reboot||nonce|not_a_ts") is None


class TestFormatRecoveryAck:
    """ACK wire format encoding tests."""

    def test_success_ack(self):
        ack = format_recovery_ack(42, "success", "jennedge restarted")
        assert ack == "RECOVER_ACK|42|success|jennedge restarted"

    def test_failed_ack(self):
        ack = format_recovery_ack(42, "failed", "permission denied")
        assert ack == "RECOVER_ACK|42|failed|permission denied"

    def test_message_truncation(self):
        long_msg = "x" * 300
        ack = format_recovery_ack(42, "success", long_msg)
        assert len(ack.encode("utf-8")) <= 256

    def test_fits_lora_limit(self):
        ack = format_recovery_ack(99999, "success", "a" * 100)
        assert len(ack.encode("utf-8")) < 256


class TestParseRecoveryAck:
    """ACK wire format decoding tests."""

    def test_round_trip(self):
        original = format_recovery_ack(42, "success", "jennedge restarted")
        parsed = parse_recovery_ack(original)
        assert parsed is not None
        assert parsed["cmd_id"] == 42
        assert parsed["status"] == "success"
        assert parsed["message"] == "jennedge restarted"

    def test_message_with_pipes(self):
        """Message field may contain pipes in diagnostic output."""
        ack = "RECOVER_ACK|42|success|uptime: 3d|mem: 512MB|disk: 80%"
        parsed = parse_recovery_ack(ack)
        assert parsed is not None
        assert parsed["message"] == "uptime: 3d|mem: 512MB|disk: 80%"

    def test_wrong_prefix(self):
        assert parse_recovery_ack("HEARTBEAT|abc") is None

    def test_too_few_fields(self):
        assert parse_recovery_ack("RECOVER_ACK|42") is None

    def test_non_integer_cmd_id(self):
        assert parse_recovery_ack("RECOVER_ACK|abc|success|msg") is None


class TestConstants:
    """Verify safety constants are correctly set."""

    def test_allowed_commands(self):
        assert ALLOWED_COMMANDS == {"reboot", "restart_service", "restart_ollama", "system_status"}

    def test_allowed_services(self):
        assert ALLOWED_SERVICES == {"jennedge", "jenn-sentry-agent", "jenn-mesh-agent", "ollama"}

    def test_admin_channel(self):
        assert ADMIN_CHANNEL_INDEX == 1

    def test_max_age(self):
        assert MAX_COMMAND_AGE_SECONDS == 300

    def test_rate_limit(self):
        assert RATE_LIMIT_SECONDS == 30
