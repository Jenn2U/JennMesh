"""Tests for the team communication manager."""

from __future__ import annotations

import json
import tempfile
from unittest.mock import MagicMock

import pytest

from jenn_mesh.core.team_comms_manager import TeamCommsManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.team_comms import MessageChannel, MessageStatus


@pytest.fixture
def db(tmp_path) -> MeshDatabase:
    return MeshDatabase(db_path=str(tmp_path / "team_test.db"))


@pytest.fixture
def manager(db) -> TeamCommsManager:
    return TeamCommsManager(db=db)


@pytest.fixture
def manager_with_mqtt(db) -> TeamCommsManager:
    mqtt = MagicMock()
    return TeamCommsManager(db=db, mqtt_client=mqtt)


# ── send_message() ──────────────────────────────────────────────────


class TestSendMessage:
    def test_send_broadcast(self, manager):
        msg = manager.send_message(channel="broadcast", sender="op1", message="Rally at CP2")
        assert msg.id is not None
        assert msg.channel == MessageChannel.BROADCAST
        assert msg.status == MessageStatus.PENDING
        assert msg.message == "Rally at CP2"

    def test_send_team_message(self, manager):
        msg = manager.send_message(channel="team", sender="op1", message="Alpha status check")
        assert msg.channel == MessageChannel.TEAM

    def test_send_direct_message(self, manager):
        msg = manager.send_message(
            channel="direct",
            sender="op1",
            message="Report in",
            recipient="!abc123",
        )
        assert msg.channel == MessageChannel.DIRECT
        assert msg.recipient == "!abc123"

    def test_direct_requires_recipient(self, manager):
        with pytest.raises(ValueError, match="require a recipient"):
            manager.send_message(channel="direct", sender="op1", message="Hello")

    def test_invalid_channel(self, manager):
        with pytest.raises(ValueError, match="Invalid channel"):
            manager.send_message(channel="invalid", sender="op1", message="Hi")

    def test_empty_message(self, manager):
        with pytest.raises(ValueError, match="cannot be empty"):
            manager.send_message(channel="broadcast", sender="op1", message="")

    def test_message_too_long(self, manager):
        with pytest.raises(ValueError, match="exceeds"):
            manager.send_message(channel="broadcast", sender="op1", message="X" * 250)

    def test_wire_format_broadcast(self, manager):
        msg = manager.send_message(channel="broadcast", sender="op1", message="Hello")
        assert msg.wire_format == "[TEAM:BROADCAST] Hello"

    def test_wire_format_direct(self, manager):
        msg = manager.send_message(
            channel="direct",
            sender="op1",
            message="Report",
            recipient="!abc",
        )
        assert msg.wire_format == "[TEAM:DIRECT] @!abc Report"

    def test_mqtt_publish_on_send(self, manager_with_mqtt):
        msg = manager_with_mqtt.send_message(channel="broadcast", sender="op1", message="Test")
        assert msg.status == MessageStatus.SENDING
        manager_with_mqtt._mqtt_client.publish.assert_called_once()
        topic, payload = manager_with_mqtt._mqtt_client.publish.call_args[0]
        assert topic == "jenn/mesh/command/team-comms"
        data = json.loads(payload)
        assert data["message"] == "Test"

    def test_mqtt_failure_marks_failed(self, manager_with_mqtt):
        manager_with_mqtt._mqtt_client.publish.side_effect = Exception("MQTT down")
        msg = manager_with_mqtt.send_message(channel="broadcast", sender="op1", message="Test")
        assert msg.status == MessageStatus.FAILED


# ── Delivery lifecycle ───────────────────────────────────────────────


class TestDeliveryLifecycle:
    def test_mark_sent(self, manager):
        msg = manager.send_message(channel="broadcast", sender="op1", message="Test")
        assert manager.mark_sent(msg.id)
        fetched = manager.get_message(msg.id)
        assert fetched["status"] == "sent"
        assert fetched.get("sent_at") is not None

    def test_mark_delivered(self, manager):
        msg = manager.send_message(channel="broadcast", sender="op1", message="Test")
        assert manager.mark_delivered(msg.id)
        fetched = manager.get_message(msg.id)
        assert fetched["status"] == "delivered"
        assert fetched.get("delivered_at") is not None

    def test_mark_nonexistent_returns_false(self, manager):
        assert not manager.mark_sent(9999)
        assert not manager.mark_delivered(9999)


# ── Listing / retrieval ──────────────────────────────────────────────


class TestListMessages:
    def test_list_all(self, manager):
        manager.send_message(channel="broadcast", sender="op1", message="A")
        manager.send_message(channel="team", sender="op2", message="B")
        msgs = manager.list_messages()
        assert len(msgs) == 2

    def test_list_by_channel(self, manager):
        manager.send_message(channel="broadcast", sender="op1", message="A")
        manager.send_message(channel="team", sender="op2", message="B")
        msgs = manager.list_messages(channel="team")
        assert len(msgs) == 1
        assert msgs[0]["channel"] == "team"

    def test_get_message(self, manager):
        msg = manager.send_message(channel="broadcast", sender="op1", message="Hello")
        fetched = manager.get_message(msg.id)
        assert fetched is not None
        assert fetched["message"] == "Hello"

    def test_get_nonexistent(self, manager):
        assert manager.get_message(9999) is None


# ── Wire text matching ───────────────────────────────────────────────


class TestFindMessageForMeshText:
    def test_matches_broadcast(self, manager):
        manager.send_message(channel="broadcast", sender="op1", message="Rally at CP2")
        result = manager.find_message_for_mesh_text("[TEAM:BROADCAST] Rally at CP2")
        assert result is not None
        assert result["message"] == "Rally at CP2"

    def test_matches_direct(self, manager):
        manager.send_message(
            channel="direct",
            sender="op1",
            message="Report in",
            recipient="!abc",
        )
        result = manager.find_message_for_mesh_text("[TEAM:DIRECT] @!abc Report in")
        assert result is not None

    def test_no_match_returns_none(self, manager):
        result = manager.find_message_for_mesh_text("[TEAM:BROADCAST] Unknown msg")
        assert result is None

    def test_non_team_prefix_returns_none(self, manager):
        assert manager.find_message_for_mesh_text("regular text") is None
