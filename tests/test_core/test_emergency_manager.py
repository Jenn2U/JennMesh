"""Tests for EmergencyBroadcastManager core logic."""

import json
import tempfile
from unittest.mock import MagicMock

import pytest

from jenn_mesh.core.emergency_manager import (
    EMERGENCY_COMMAND_TOPIC,
    EmergencyBroadcastManager,
)
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.emergency import BroadcastStatus, EmergencyType


@pytest.fixture
def db() -> MeshDatabase:
    """Create a temporary in-memory test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def mqtt_client() -> MagicMock:
    """Mock MQTT client."""
    return MagicMock()


@pytest.fixture
def manager(db: MeshDatabase, mqtt_client: MagicMock) -> EmergencyBroadcastManager:
    """Create an EmergencyBroadcastManager with a mock MQTT client."""
    return EmergencyBroadcastManager(db=db, mqtt_client=mqtt_client)


@pytest.fixture
def manager_no_mqtt(db: MeshDatabase) -> EmergencyBroadcastManager:
    """Create an EmergencyBroadcastManager without MQTT."""
    return EmergencyBroadcastManager(db=db, mqtt_client=None)


class TestCreateBroadcast:
    """Tests for creating emergency broadcasts."""

    def test_create_broadcast_success(self, manager: EmergencyBroadcastManager) -> None:
        broadcast = manager.create_broadcast(
            broadcast_type="evacuation",
            message="Fire alarm. Evacuate now.",
            sender="operator-1",
            confirmed=True,
        )
        assert broadcast.id is not None
        assert broadcast.id > 0
        assert broadcast.broadcast_type == EmergencyType.EVACUATION
        assert broadcast.message == "Fire alarm. Evacuate now."
        assert broadcast.sender == "operator-1"
        assert broadcast.status == BroadcastStatus.PENDING
        assert broadcast.confirmed is True

    def test_create_broadcast_requires_confirmation(
        self, manager: EmergencyBroadcastManager
    ) -> None:
        with pytest.raises(ValueError, match="explicit confirmation"):
            manager.create_broadcast(
                broadcast_type="evacuation",
                message="Fire alarm.",
                confirmed=False,
            )

    def test_create_broadcast_invalid_type(self, manager: EmergencyBroadcastManager) -> None:
        with pytest.raises(ValueError, match="Invalid emergency type"):
            manager.create_broadcast(
                broadcast_type="alien_invasion",
                message="They're here.",
                confirmed=True,
            )

    def test_create_broadcast_empty_message(self, manager: EmergencyBroadcastManager) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            manager.create_broadcast(
                broadcast_type="evacuation",
                message="   ",
                confirmed=True,
            )

    def test_create_broadcast_stores_in_db(
        self, manager: EmergencyBroadcastManager, db: MeshDatabase
    ) -> None:
        broadcast = manager.create_broadcast(
            broadcast_type="security_alert",
            message="Unauthorized access.",
            confirmed=True,
        )
        stored = db.get_broadcast(broadcast.id)
        assert stored is not None
        assert stored["broadcast_type"] == "security_alert"
        assert stored["message"] == "Unauthorized access."
        assert stored["status"] == "pending"

    def test_create_broadcast_default_sender(self, manager: EmergencyBroadcastManager) -> None:
        broadcast = manager.create_broadcast(
            broadcast_type="all_clear",
            message="Situation resolved.",
            confirmed=True,
        )
        assert broadcast.sender == "dashboard"


class TestMQTTPublish:
    """Tests for MQTT command publishing."""

    def test_publishes_to_command_topic(
        self, manager: EmergencyBroadcastManager, mqtt_client: MagicMock
    ) -> None:
        manager.create_broadcast(
            broadcast_type="evacuation",
            message="Fire alarm.",
            confirmed=True,
        )
        mqtt_client.publish.assert_called_once()
        topic = mqtt_client.publish.call_args[0][0]
        assert topic == EMERGENCY_COMMAND_TOPIC

    def test_command_payload_structure(
        self, manager: EmergencyBroadcastManager, mqtt_client: MagicMock
    ) -> None:
        manager.create_broadcast(
            broadcast_type="network_down",
            message="Cloud lost.",
            confirmed=True,
        )
        payload_str = mqtt_client.publish.call_args[0][1]
        payload = json.loads(payload_str)
        assert "broadcast_id" in payload
        assert payload["type"] == "network_down"
        assert payload["message"] == "Cloud lost."
        assert payload["channel_index"] == 3
        assert payload["mesh_text"] == "[EMERGENCY:NETWORK_DOWN] Cloud lost."

    def test_no_mqtt_stores_but_does_not_send(
        self, manager_no_mqtt: EmergencyBroadcastManager, db: MeshDatabase
    ) -> None:
        broadcast = manager_no_mqtt.create_broadcast(
            broadcast_type="evacuation",
            message="Fire alarm.",
            confirmed=True,
        )
        # Should still be stored in DB
        stored = db.get_broadcast(broadcast.id)
        assert stored is not None
        assert stored["status"] == "pending"

    def test_mqtt_failure_marks_broadcast_failed(
        self, manager: EmergencyBroadcastManager, mqtt_client: MagicMock, db: MeshDatabase
    ) -> None:
        mqtt_client.publish.side_effect = Exception("Connection refused")
        broadcast = manager.create_broadcast(
            broadcast_type="evacuation",
            message="Fire alarm.",
            confirmed=True,
        )
        stored = db.get_broadcast(broadcast.id)
        assert stored["status"] == "failed"


class TestStatusTransitions:
    """Tests for broadcast status lifecycle transitions."""

    def test_mark_sent(self, manager: EmergencyBroadcastManager, db: MeshDatabase) -> None:
        broadcast = manager.create_broadcast(
            broadcast_type="evacuation",
            message="Fire alarm.",
            confirmed=True,
        )
        manager.mark_sent(broadcast.id)
        stored = db.get_broadcast(broadcast.id)
        assert stored["status"] == "sent"
        assert stored["sent_at"] is not None

    def test_mark_delivered(self, manager: EmergencyBroadcastManager, db: MeshDatabase) -> None:
        broadcast = manager.create_broadcast(
            broadcast_type="evacuation",
            message="Fire alarm.",
            confirmed=True,
        )
        manager.mark_sent(broadcast.id)
        manager.mark_delivered(broadcast.id)
        stored = db.get_broadcast(broadcast.id)
        assert stored["status"] == "delivered"
        assert stored["delivered_at"] is not None
        assert stored["mesh_received"] == 1

    def test_mark_failed(self, manager: EmergencyBroadcastManager, db: MeshDatabase) -> None:
        broadcast = manager.create_broadcast(
            broadcast_type="evacuation",
            message="Fire alarm.",
            confirmed=True,
        )
        manager.mark_failed(broadcast.id)
        stored = db.get_broadcast(broadcast.id)
        assert stored["status"] == "failed"


class TestBroadcastQueries:
    """Tests for listing and querying broadcasts."""

    def test_list_broadcasts(self, manager: EmergencyBroadcastManager) -> None:
        manager.create_broadcast(broadcast_type="evacuation", message="First.", confirmed=True)
        manager.create_broadcast(broadcast_type="all_clear", message="Second.", confirmed=True)
        broadcasts = manager.list_broadcasts()
        assert len(broadcasts) == 2
        # Verify both are returned (ordering within same second is deterministic by
        # created_at DESC — when timestamps tie, SQLite returns in rowid order which
        # is ascending, so after DESC sort they may come back in either order)
        messages = {b["message"] for b in broadcasts}
        assert messages == {"First.", "Second."}

    def test_list_broadcasts_limit(self, manager: EmergencyBroadcastManager) -> None:
        for i in range(5):
            manager.create_broadcast(broadcast_type="custom", message=f"Alert {i}.", confirmed=True)
        broadcasts = manager.list_broadcasts(limit=3)
        assert len(broadcasts) == 3

    def test_get_broadcast(self, manager: EmergencyBroadcastManager) -> None:
        broadcast = manager.create_broadcast(
            broadcast_type="severe_weather",
            message="Tornado warning.",
            confirmed=True,
        )
        stored = manager.get_broadcast(broadcast.id)
        assert stored is not None
        assert stored["broadcast_type"] == "severe_weather"

    def test_get_broadcast_not_found(self, manager: EmergencyBroadcastManager) -> None:
        assert manager.get_broadcast(9999) is None


class TestFleetEmergencyStatus:
    """Tests for fleet-level emergency status summary."""

    def test_empty_fleet_status(self, manager: EmergencyBroadcastManager) -> None:
        status = manager.get_fleet_emergency_status()
        assert status["active_broadcasts"] == 0
        assert status["last_broadcast_time"] is None
        assert status["recent_count"] == 0

    def test_fleet_status_with_active_broadcast(self, manager: EmergencyBroadcastManager) -> None:
        manager.create_broadcast(broadcast_type="evacuation", message="Fire.", confirmed=True)
        status = manager.get_fleet_emergency_status()
        assert status["active_broadcasts"] == 1
        assert status["last_broadcast_time"] is not None
        assert status["recent_count"] == 1

    def test_fleet_status_delivered_not_active(self, manager: EmergencyBroadcastManager) -> None:
        broadcast = manager.create_broadcast(
            broadcast_type="evacuation", message="Fire.", confirmed=True
        )
        manager.mark_delivered(broadcast.id)
        status = manager.get_fleet_emergency_status()
        assert status["active_broadcasts"] == 0


class TestFindBroadcastForMeshText:
    """Tests for matching mesh echo texts to original broadcasts."""

    def test_find_matching_broadcast(self, manager: EmergencyBroadcastManager) -> None:
        manager.create_broadcast(broadcast_type="evacuation", message="Fire.", confirmed=True)
        result = manager.find_broadcast_for_mesh_text("evacuation")
        assert result is not None
        assert result["broadcast_type"] == "evacuation"

    def test_no_match_for_different_type(self, manager: EmergencyBroadcastManager) -> None:
        manager.create_broadcast(broadcast_type="evacuation", message="Fire.", confirmed=True)
        result = manager.find_broadcast_for_mesh_text("severe_weather")
        assert result is None

    def test_delivered_broadcast_not_matched(self, manager: EmergencyBroadcastManager) -> None:
        broadcast = manager.create_broadcast(
            broadcast_type="evacuation", message="Fire.", confirmed=True
        )
        manager.mark_delivered(broadcast.id)
        result = manager.find_broadcast_for_mesh_text("evacuation")
        assert result is None
