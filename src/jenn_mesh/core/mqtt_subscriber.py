"""MQTT subscriber — ingests telemetry from dedicated mesh broker into SQLite."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)

# Default MQTT topic pattern: jenn/mesh/{region}/json/{packet_type}/{nodeId}
DEFAULT_TOPIC = "jenn/mesh/#"


class MQTTSubscriber:
    """Subscribes to the dedicated mesh MQTT broker and ingests telemetry.

    Processes:
        - NodeInfo packets -> updates device registry
        - Position packets -> updates GPS positions
        - Telemetry packets -> updates battery, signal, environment data
    """

    def __init__(
        self,
        db: MeshDatabase,
        broker: str = "mqtt.jenn2u.ai",
        port: int = 1884,
        username: Optional[str] = "jenn-mesh",
        password: Optional[str] = None,
        topic: str = DEFAULT_TOPIC,
    ):
        self.db = db
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.topic = topic
        self._client: Any = None
        self._running = False
        self._on_device_update: Optional[Callable] = None
        self._on_position_update: Optional[Callable] = None
        self._on_alert: Optional[Callable] = None
        self._on_topology_update: Optional[Callable] = None

    def set_callbacks(
        self,
        on_device_update: Optional[Callable] = None,
        on_position_update: Optional[Callable] = None,
        on_alert: Optional[Callable] = None,
        on_topology_update: Optional[Callable] = None,
    ) -> None:
        """Register optional callbacks for real-time event hooks."""
        self._on_device_update = on_device_update
        self._on_position_update = on_position_update
        self._on_alert = on_alert
        self._on_topology_update = on_topology_update

    def start(self) -> bool:
        """Connect to broker and start listening for mesh telemetry."""
        try:
            import paho.mqtt.client as mqtt

            self._client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="jenn-mesh-subscriber",
            )
            if self.username:
                self._client.username_pw_set(self.username, self.password or "")

            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect

            self._client.connect(self.broker, self.port)
            self._client.loop_start()
            self._running = True
            logger.info("MQTT subscriber connected to %s:%d", self.broker, self.port)
            return True

        except ImportError:
            logger.error("paho-mqtt not installed — pip install 'jenn-mesh[dashboard]'")
            return False
        except Exception as e:
            logger.error("MQTT connection failed: %s", e)
            return False

    def stop(self) -> None:
        """Disconnect from the MQTT broker."""
        self._running = False
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            logger.info("MQTT subscriber disconnected")

    @property
    def is_running(self) -> bool:
        return self._running

    def _on_connect(
        self, client: Any, userdata: Any, flags: Any, rc: Any, properties: Any = None
    ) -> None:
        """Callback when connected to broker — subscribe to topic."""
        logger.info("Connected to MQTT broker, subscribing to %s", self.topic)
        client.subscribe(self.topic)

    def _on_disconnect(
        self, client: Any, userdata: Any, flags: Any, rc: Any, properties: Any = None
    ) -> None:
        """Callback when disconnected from broker."""
        if self._running:
            logger.warning("MQTT disconnected (rc=%s), will auto-reconnect", rc)

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """Process an incoming MQTT message from the mesh network."""
        try:
            topic_parts = msg.topic.split("/")
            # Expected: jenn/mesh/{region}/json/{packet_type}/{nodeId}
            if len(topic_parts) < 6:
                return

            packet_type = topic_parts[4]
            node_id = topic_parts[5]
            payload = json.loads(msg.payload.decode("utf-8"))

            if packet_type == "nodeinfo":
                self._handle_nodeinfo(node_id, payload)
            elif packet_type == "position":
                self._handle_position(node_id, payload)
            elif packet_type == "telemetry":
                self._handle_telemetry(node_id, payload)
            elif packet_type == "neighborinfo":
                self._handle_neighborinfo(node_id, payload)

        except json.JSONDecodeError:
            logger.debug("Non-JSON MQTT payload on %s", msg.topic)
        except Exception as e:
            logger.error("Error processing MQTT message: %s", e)

    def _handle_nodeinfo(self, node_id: str, payload: dict) -> None:
        """Process a NodeInfo packet — update device registry."""
        user = payload.get("user", payload)
        self.db.upsert_device(
            node_id=node_id,
            long_name=user.get("longName") or user.get("long_name"),
            short_name=user.get("shortName") or user.get("short_name"),
            hw_model=user.get("hwModel") or user.get("hw_model"),
            role=user.get("role"),
            last_seen=datetime.utcnow().isoformat(),
        )
        logger.debug("NodeInfo updated: %s", node_id)
        if self._on_device_update:
            self._on_device_update(node_id)

    def _handle_position(self, node_id: str, payload: dict) -> None:
        """Process a Position packet — store GPS coordinates."""
        lat = payload.get("latitude") or payload.get("latitudeI")
        lon = payload.get("longitude") or payload.get("longitudeI")

        if lat is None or lon is None:
            return

        # Meshtastic sometimes sends latitudeI as integer (1e-7 degrees)
        if isinstance(lat, int) and abs(lat) > 1000:
            lat = lat * 1e-7
        if isinstance(lon, int) and abs(lon) > 1000:
            lon = lon * 1e-7

        altitude = payload.get("altitude")
        precision = payload.get("precisionBits") or payload.get("precision_bits")

        self.db.add_position(
            node_id=node_id,
            latitude=float(lat),
            longitude=float(lon),
            altitude=float(altitude) if altitude else None,
            precision_bits=precision,
            source="gps",
        )

        # Also update the device's current position
        self.db.upsert_device(
            node_id=node_id,
            latitude=float(lat),
            longitude=float(lon),
            altitude=float(altitude) if altitude else None,
            last_seen=datetime.utcnow().isoformat(),
        )

        logger.debug("Position updated: %s (%.6f, %.6f)", node_id, lat, lon)
        if self._on_position_update:
            self._on_position_update(node_id, float(lat), float(lon))

    def _handle_telemetry(self, node_id: str, payload: dict) -> None:
        """Process a Telemetry packet — update battery, signal, environment."""
        updates: dict[str, Any] = {"last_seen": datetime.utcnow().isoformat()}

        # Device metrics
        if "battery_level" in payload or "batteryLevel" in payload:
            level = payload.get("battery_level") or payload.get("batteryLevel")
            if level is not None:
                updates["battery_level"] = int(level)

        if "voltage" in payload:
            updates["voltage"] = float(payload["voltage"])

        if "channel_utilization" in payload or "channelUtilization" in payload:
            # Store as signal_snr for now (TODO: dedicated field in v0.2.0)
            pass

        self.db.upsert_device(node_id=node_id, **updates)
        logger.debug("Telemetry updated: %s", node_id)
        if self._on_device_update:
            self._on_device_update(node_id)

    def _handle_neighborinfo(self, node_id: str, payload: dict) -> None:
        """Process a NeighborInfo packet — update topology edges.

        Meshtastic NEIGHBORINFO format:
        {"neighborinfo": {"node_id": 681738328, "neighbors": [
            {"node_id": 681738400, "snr": 10.5}, ...]}}
        """
        from jenn_mesh.core.topology import TopologyManager

        info = payload.get("neighborinfo", payload)
        raw_neighbors = info.get("neighbors", [])

        # Convert integer node IDs to hex ! format
        neighbors = []
        for n in raw_neighbors:
            raw_id = n.get("node_id", n.get("nodeId"))
            if raw_id is None:
                continue
            if isinstance(raw_id, int):
                hex_id = f"!{raw_id:08x}"
            else:
                hex_id = str(raw_id)
            neighbors.append({"node_id": hex_id, "snr": n.get("snr"), "rssi": n.get("rssi")})

        manager = TopologyManager(self.db)
        manager.update_neighbors(node_id, neighbors)

        logger.debug("NeighborInfo updated: %s reported %d neighbors", node_id, len(neighbors))
        if self._on_topology_update:
            self._on_topology_update(node_id)
