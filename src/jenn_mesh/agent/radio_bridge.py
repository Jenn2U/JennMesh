"""Radio bridge — serial/TCP connection to local Meshtastic radio."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Packet type constants
PACKET_NODEINFO = "nodeinfo"
PACKET_POSITION = "position"
PACKET_TELEMETRY = "telemetry"
PACKET_TEXT = "text"


class RadioBridge:
    """Wraps the meshtastic Python library to connect to a local radio.

    Supports serial (USB), TCP, and BLE connections. Subscribes to mesh
    packets and forwards telemetry to callbacks (typically MQTT publish).
    """

    def __init__(
        self,
        port: Optional[str] = None,
        host: Optional[str] = None,
        ble_address: Optional[str] = None,
    ):
        """Initialize radio bridge.

        Provide exactly one of: port (serial), host (TCP), ble_address.

        Args:
            port: Serial port, e.g., "/dev/ttyUSB0".
            host: TCP host:port, e.g., "10.10.50.100:4403".
            ble_address: BLE address or name, e.g., "Meshtastic_1234".
        """
        self.port = port
        self.host = host
        self.ble_address = ble_address
        self._interface: Any = None
        self._callbacks: dict[str, list[Callable]] = {
            PACKET_NODEINFO: [],
            PACKET_POSITION: [],
            PACKET_TELEMETRY: [],
            PACKET_TEXT: [],
        }
        self._running = False

    def connect(self) -> bool:
        """Establish connection to the radio."""
        try:
            import meshtastic
            import meshtastic.serial_interface
            import meshtastic.tcp_interface

            if self.port:
                self._interface = meshtastic.serial_interface.SerialInterface(
                    self.port
                )
            elif self.host:
                host_parts = self.host.split(":")
                hostname = host_parts[0]
                port = int(host_parts[1]) if len(host_parts) > 1 else 4403
                self._interface = meshtastic.tcp_interface.TCPInterface(
                    hostname=hostname, portNumber=port
                )
            else:
                # Auto-detect serial
                self._interface = meshtastic.serial_interface.SerialInterface()

            self._setup_subscriptions()
            self._running = True
            logger.info("Radio bridge connected")
            return True

        except Exception as e:
            logger.error("Failed to connect to radio: %s", e)
            return False

    def disconnect(self) -> None:
        """Disconnect from the radio."""
        self._running = False
        if self._interface:
            try:
                self._interface.close()
            except Exception as e:
                logger.warning("Error closing interface: %s", e)
            self._interface = None
        logger.info("Radio bridge disconnected")

    def on_packet(self, packet_type: str, callback: Callable) -> None:
        """Register a callback for a packet type."""
        if packet_type in self._callbacks:
            self._callbacks[packet_type].append(callback)

    def send_text(self, text: str, destination: Optional[str] = None) -> bool:
        """Send a text message to the mesh or a specific node."""
        if not self._interface:
            return False
        try:
            if destination:
                self._interface.sendText(text, destinationId=destination)
            else:
                self._interface.sendText(text)
            return True
        except Exception as e:
            logger.error("Failed to send text: %s", e)
            return False

    def get_node_info(self) -> dict[str, Any]:
        """Get info about all known nodes from the local radio's node database."""
        if not self._interface:
            return {}
        try:
            nodes = self._interface.nodes or {}
            return {
                node_id: {
                    "num": info.get("num"),
                    "user": info.get("user", {}),
                    "position": info.get("position", {}),
                    "lastHeard": info.get("lastHeard"),
                    "snr": info.get("snr"),
                }
                for node_id, info in nodes.items()
            }
        except Exception as e:
            logger.error("Failed to get node info: %s", e)
            return {}

    @property
    def is_connected(self) -> bool:
        """Check if radio bridge is currently connected."""
        return self._interface is not None and self._running

    def _setup_subscriptions(self) -> None:
        """Subscribe to mesh packet events from the meshtastic library."""
        from pubsub import pub

        def on_receive(packet: dict, interface: Any) -> None:
            self._handle_packet(packet)

        pub.subscribe(on_receive, "meshtastic.receive")

    def _handle_packet(self, packet: dict) -> None:
        """Route an incoming mesh packet to registered callbacks."""
        try:
            decoded = packet.get("decoded", {})
            portnum = decoded.get("portnum", "")

            if portnum == "NODEINFO_APP":
                ptype = PACKET_NODEINFO
            elif portnum == "POSITION_APP":
                ptype = PACKET_POSITION
            elif portnum == "TELEMETRY_APP":
                ptype = PACKET_TELEMETRY
            elif portnum == "TEXT_MESSAGE_APP":
                ptype = PACKET_TEXT
            else:
                return

            enriched = {
                "type": ptype,
                "from": packet.get("fromId", ""),
                "to": packet.get("toId", ""),
                "payload": decoded.get("payload", decoded),
                "snr": packet.get("rxSnr"),
                "rssi": packet.get("rxRssi"),
                "hop_limit": packet.get("hopLimit"),
                "timestamp": datetime.utcnow().isoformat(),
                "raw": packet,
            }

            for callback in self._callbacks.get(ptype, []):
                try:
                    callback(enriched)
                except Exception as e:
                    logger.error("Callback error for %s: %s", ptype, e)

        except Exception as e:
            logger.error("Packet handling error: %s", e)
