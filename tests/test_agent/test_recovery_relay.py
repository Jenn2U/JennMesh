"""Tests for RecoveryRelay — gateway-agent-side MQTT-to-mesh relay."""

import json
from unittest.mock import MagicMock

import pytest

from jenn_mesh.agent.recovery_relay import (
    RECOVERY_ACK_TOPIC,
    RECOVERY_COMMAND_TOPIC,
    RecoveryRelay,
)
from jenn_mesh.models.recovery import ADMIN_CHANNEL_INDEX


@pytest.fixture
def bridge() -> MagicMock:
    """Mock RadioBridge with send_text method."""
    mock = MagicMock()
    mock.send_text.return_value = True
    return mock


@pytest.fixture
def mqtt_client() -> MagicMock:
    """Mock MQTT client."""
    return MagicMock()


@pytest.fixture
def relay(bridge: MagicMock, mqtt_client: MagicMock) -> RecoveryRelay:
    """Create a RecoveryRelay with mock bridge and MQTT."""
    return RecoveryRelay(bridge=bridge, mqtt_client=mqtt_client)


def _make_mqtt_msg(payload: dict) -> MagicMock:
    """Create a mock MQTT message with JSON payload."""
    msg = MagicMock()
    msg.payload = json.dumps(payload).encode()
    return msg


class TestRelayLifecycle:
    """Tests for start/stop lifecycle."""

    def test_start_subscribes_to_topic(self, relay: RecoveryRelay, mqtt_client: MagicMock) -> None:
        relay.start()
        mqtt_client.subscribe.assert_called_once_with(RECOVERY_COMMAND_TOPIC)
        mqtt_client.message_callback_add.assert_called_once()
        assert relay.is_running is True

    def test_stop_unsubscribes(self, relay: RecoveryRelay, mqtt_client: MagicMock) -> None:
        relay.start()
        relay.stop()
        mqtt_client.unsubscribe.assert_called_once_with(RECOVERY_COMMAND_TOPIC)
        assert relay.is_running is False

    def test_start_without_mqtt_is_inactive(self, bridge: MagicMock) -> None:
        relay = RecoveryRelay(bridge=bridge, mqtt_client=None)
        relay.start()
        assert relay.is_running is False


class TestOnCommand:
    """Tests for MQTT command → mesh relay."""

    def test_relays_command_to_mesh(self, relay: RecoveryRelay, bridge: MagicMock) -> None:
        msg = _make_mqtt_msg(
            {
                "command_id": 42,
                "target_node_id": "!a1b2c3d4",
                "command_type": "reboot",
                "args": "",
                "nonce": "abcd1234",
                "mesh_text": "RECOVER|42|reboot||abcd1234|1709395200",
                "channel_index": 1,
            }
        )
        relay._on_command(None, None, msg)

        bridge.send_text.assert_called_once_with(
            "RECOVER|42|reboot||abcd1234|1709395200",
            destination="!a1b2c3d4",
            channel_index=1,
        )

    def test_publishes_relay_ack_on_success(
        self, relay: RecoveryRelay, bridge: MagicMock, mqtt_client: MagicMock
    ) -> None:
        msg = _make_mqtt_msg(
            {
                "command_id": 42,
                "target_node_id": "!a1b2c3d4",
                "mesh_text": "RECOVER|42|reboot||abcd1234|1709395200",
                "channel_index": 1,
            }
        )
        relay._on_command(None, None, msg)

        # Should publish relay ACK to MQTT
        ack_call = mqtt_client.publish.call_args
        assert ack_call[0][0] == RECOVERY_ACK_TOPIC
        ack_payload = json.loads(ack_call[0][1])
        assert ack_payload["command_id"] == 42
        assert ack_payload["relay_status"] == "relayed"
        assert ack_payload["source"] == "gateway_relay"

    def test_publishes_relay_failure_on_send_fail(
        self, relay: RecoveryRelay, bridge: MagicMock, mqtt_client: MagicMock
    ) -> None:
        bridge.send_text.return_value = False
        msg = _make_mqtt_msg(
            {
                "command_id": 99,
                "target_node_id": "!deadbeef",
                "mesh_text": "RECOVER|99|system_status||beef1234|1709395200",
                "channel_index": 1,
            }
        )
        relay._on_command(None, None, msg)

        ack_payload = json.loads(mqtt_client.publish.call_args[0][1])
        assert ack_payload["relay_status"] == "relay_failed"

    def test_handles_invalid_json_gracefully(self, relay: RecoveryRelay, bridge: MagicMock) -> None:
        msg = MagicMock()
        msg.payload = b"not json"
        relay._on_command(None, None, msg)  # Should not raise
        bridge.send_text.assert_not_called()

    def test_handles_missing_mesh_text(self, relay: RecoveryRelay, bridge: MagicMock) -> None:
        msg = _make_mqtt_msg({"command_id": 42, "target_node_id": "!abc"})
        relay._on_command(None, None, msg)  # Should not raise
        bridge.send_text.assert_not_called()

    def test_default_channel_index(self, relay: RecoveryRelay, bridge: MagicMock) -> None:
        """If channel_index is missing from payload, default to ADMIN_CHANNEL_INDEX."""
        msg = _make_mqtt_msg(
            {
                "command_id": 1,
                "target_node_id": "!abc",
                "mesh_text": "RECOVER|1|reboot||nonce123|123456",
            }
        )
        relay._on_command(None, None, msg)
        kwargs = bridge.send_text.call_args[1]
        assert kwargs["channel_index"] == ADMIN_CHANNEL_INDEX


class TestHandleMeshText:
    """Tests for RECOVER_ACK mesh text → MQTT forwarding."""

    def test_forwards_ack_to_mqtt(self, relay: RecoveryRelay, mqtt_client: MagicMock) -> None:
        result = relay.handle_mesh_text(
            "RECOVER_ACK|42|success|jennedge restarted", from_id="!target01"
        )
        assert result is True

        ack_payload = json.loads(mqtt_client.publish.call_args[0][1])
        assert ack_payload["command_id"] == 42
        assert ack_payload["status"] == "success"
        assert ack_payload["message"] == "jennedge restarted"
        assert ack_payload["from_node_id"] == "!target01"
        assert ack_payload["source"] == "target_agent"

    def test_ignores_non_ack_text(self, relay: RecoveryRelay) -> None:
        assert relay.handle_mesh_text("HEARTBEAT|node|3600|ok|85|ts") is False

    def test_handles_malformed_ack(self, relay: RecoveryRelay, mqtt_client: MagicMock) -> None:
        result = relay.handle_mesh_text("RECOVER_ACK|bad")
        assert result is True  # Was an ACK attempt, consumed
        mqtt_client.publish.assert_not_called()

    def test_ack_with_pipes_in_message(self, relay: RecoveryRelay, mqtt_client: MagicMock) -> None:
        """ACK messages may contain diagnostic output with pipes."""
        ack = "RECOVER_ACK|42|success|up:3d|disk:42%|mem:1024M/3840M"
        result = relay.handle_mesh_text(ack, from_id="!target01")
        assert result is True

        ack_payload = json.loads(mqtt_client.publish.call_args[0][1])
        assert ack_payload["message"] == "up:3d|disk:42%|mem:1024M/3840M"
