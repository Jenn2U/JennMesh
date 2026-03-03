"""Tests for emergency broadcast models."""

import pytest

from jenn_mesh.models.emergency import (
    EMERGENCY_CHANNEL_INDEX,
    MAX_MESSAGE_LENGTH,
    BroadcastStatus,
    EmergencyBroadcast,
    EmergencyType,
)
from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertSeverity, AlertType


class TestEmergencyType:
    """Tests for EmergencyType enum."""

    def test_all_types_defined(self) -> None:
        expected = {
            "evacuation",
            "network_down",
            "severe_weather",
            "security_alert",
            "all_clear",
            "custom",
        }
        assert {t.value for t in EmergencyType} == expected

    def test_string_enum_values(self) -> None:
        assert EmergencyType.EVACUATION == "evacuation"
        assert EmergencyType.NETWORK_DOWN == "network_down"
        assert EmergencyType.ALL_CLEAR == "all_clear"

    def test_from_string(self) -> None:
        assert EmergencyType("evacuation") == EmergencyType.EVACUATION
        assert EmergencyType("custom") == EmergencyType.CUSTOM


class TestBroadcastStatus:
    """Tests for BroadcastStatus enum."""

    def test_all_statuses_defined(self) -> None:
        expected = {"pending", "sending", "sent", "delivered", "failed"}
        assert {s.value for s in BroadcastStatus} == expected

    def test_lifecycle_order(self) -> None:
        """Verify the expected status progression exists."""
        statuses = [s.value for s in BroadcastStatus]
        assert "pending" in statuses
        assert "sending" in statuses
        assert "sent" in statuses
        assert "delivered" in statuses
        assert "failed" in statuses


class TestEmergencyBroadcast:
    """Tests for EmergencyBroadcast model."""

    def test_create_minimal(self) -> None:
        broadcast = EmergencyBroadcast(
            broadcast_type=EmergencyType.EVACUATION,
            message="Fire alarm. Evacuate now.",
        )
        assert broadcast.broadcast_type == EmergencyType.EVACUATION
        assert broadcast.message == "Fire alarm. Evacuate now."
        assert broadcast.sender == "dashboard"
        assert broadcast.channel_index == EMERGENCY_CHANNEL_INDEX
        assert broadcast.status == BroadcastStatus.PENDING
        assert broadcast.confirmed is False
        assert broadcast.mesh_received is False
        assert broadcast.sent_at is None
        assert broadcast.delivered_at is None

    def test_create_with_all_fields(self) -> None:
        broadcast = EmergencyBroadcast(
            broadcast_type=EmergencyType.SECURITY_ALERT,
            message="Unauthorized access detected.",
            sender="operator-1",
            channel_index=3,
            confirmed=True,
        )
        assert broadcast.sender == "operator-1"
        assert broadcast.confirmed is True

    def test_message_validation_empty(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            EmergencyBroadcast(
                broadcast_type=EmergencyType.CUSTOM,
                message="   ",
            )

    def test_message_validation_too_long(self) -> None:
        long_message = "x" * (MAX_MESSAGE_LENGTH + 1)
        with pytest.raises(ValueError, match="too long"):
            EmergencyBroadcast(
                broadcast_type=EmergencyType.CUSTOM,
                message=long_message,
            )

    def test_message_at_max_length(self) -> None:
        """Exactly MAX_MESSAGE_LENGTH chars should be valid."""
        msg = "a" * MAX_MESSAGE_LENGTH
        broadcast = EmergencyBroadcast(
            broadcast_type=EmergencyType.CUSTOM,
            message=msg,
        )
        assert len(broadcast.message) == MAX_MESSAGE_LENGTH

    def test_message_whitespace_trimmed(self) -> None:
        broadcast = EmergencyBroadcast(
            broadcast_type=EmergencyType.ALL_CLEAR,
            message="  Situation resolved.  ",
        )
        assert broadcast.message == "Situation resolved."

    def test_is_active_pending(self) -> None:
        broadcast = EmergencyBroadcast(
            broadcast_type=EmergencyType.EVACUATION,
            message="Test",
            status=BroadcastStatus.PENDING,
        )
        assert broadcast.is_active is True

    def test_is_active_sending(self) -> None:
        broadcast = EmergencyBroadcast(
            broadcast_type=EmergencyType.EVACUATION,
            message="Test",
            status=BroadcastStatus.SENDING,
        )
        assert broadcast.is_active is True

    def test_is_active_sent(self) -> None:
        broadcast = EmergencyBroadcast(
            broadcast_type=EmergencyType.EVACUATION,
            message="Test",
            status=BroadcastStatus.SENT,
        )
        assert broadcast.is_active is True

    def test_is_not_active_delivered(self) -> None:
        broadcast = EmergencyBroadcast(
            broadcast_type=EmergencyType.EVACUATION,
            message="Test",
            status=BroadcastStatus.DELIVERED,
        )
        assert broadcast.is_active is False

    def test_is_not_active_failed(self) -> None:
        broadcast = EmergencyBroadcast(
            broadcast_type=EmergencyType.EVACUATION,
            message="Test",
            status=BroadcastStatus.FAILED,
        )
        assert broadcast.is_active is False


class TestFormatMeshText:
    """Tests for the wire format builder/parser."""

    def test_format_evacuation(self) -> None:
        text = EmergencyBroadcast.format_mesh_text(
            EmergencyType.EVACUATION,
            "Building 3 fire alarm. Evacuate immediately.",
        )
        assert text == "[EMERGENCY:EVACUATION] Building 3 fire alarm. Evacuate immediately."

    def test_format_network_down(self) -> None:
        text = EmergencyBroadcast.format_mesh_text(
            EmergencyType.NETWORK_DOWN,
            "Cloud connectivity lost.",
        )
        assert text == "[EMERGENCY:NETWORK_DOWN] Cloud connectivity lost."

    def test_format_custom(self) -> None:
        text = EmergencyBroadcast.format_mesh_text(
            EmergencyType.CUSTOM,
            "Custom alert message.",
        )
        assert text == "[EMERGENCY:CUSTOM] Custom alert message."

    def test_parse_valid(self) -> None:
        result = EmergencyBroadcast.parse_mesh_text("[EMERGENCY:EVACUATION] Building 3 fire alarm.")
        assert result is not None
        assert result[0] == "evacuation"
        assert result[1] == "Building 3 fire alarm."

    def test_parse_network_down(self) -> None:
        result = EmergencyBroadcast.parse_mesh_text("[EMERGENCY:NETWORK_DOWN] Cloud lost.")
        assert result is not None
        assert result[0] == "network_down"
        assert result[1] == "Cloud lost."

    def test_parse_not_emergency(self) -> None:
        assert EmergencyBroadcast.parse_mesh_text("Hello world") is None

    def test_parse_heartbeat(self) -> None:
        assert (
            EmergencyBroadcast.parse_mesh_text("HEARTBEAT|!abc|120|edge:ok|85|2026-01-01") is None
        )

    def test_parse_malformed_no_bracket(self) -> None:
        assert EmergencyBroadcast.parse_mesh_text("[EMERGENCY:EVACUATION No bracket") is None

    def test_format_roundtrip(self) -> None:
        """format → parse should recover type and message."""
        original_type = EmergencyType.SEVERE_WEATHER
        original_msg = "Tornado warning in effect."
        text = EmergencyBroadcast.format_mesh_text(original_type, original_msg)
        result = EmergencyBroadcast.parse_mesh_text(text)
        assert result is not None
        assert result[0] == original_type.value
        assert result[1] == original_msg


class TestFleetAlertTypeIntegration:
    """Verify EMERGENCY_BROADCAST is wired into the fleet alert system."""

    def test_alert_type_exists(self) -> None:
        assert AlertType.EMERGENCY_BROADCAST == "emergency_broadcast"

    def test_severity_is_critical(self) -> None:
        assert ALERT_SEVERITY_MAP[AlertType.EMERGENCY_BROADCAST] == AlertSeverity.CRITICAL
