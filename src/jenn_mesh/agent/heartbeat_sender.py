"""Heartbeat sender — builds and sends periodic heartbeat text messages over LoRa."""

from __future__ import annotations

import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

# Default heartbeat interval: 120 seconds (2 minutes)
DEFAULT_HEARTBEAT_INTERVAL = 120


class HeartbeatSender:
    """Sends periodic heartbeat text messages over the mesh radio.

    The heartbeat message format:
        HEARTBEAT|{nodeId}|{uptime_s}|{services}|{battery}|{timestamp}

    Example:
        HEARTBEAT|!28979058|3600|edge:ok,mqtt:down,internet:down|85|2026-03-02T15:30:00Z

    Usage:
        sender = HeartbeatSender(node_id="!28979058", bridge=radio_bridge)
        sender.maybe_send(health_monitor)  # Call every loop tick
    """

    def __init__(
        self,
        node_id: str,
        bridge: object,
        interval: int = DEFAULT_HEARTBEAT_INTERVAL,
    ):
        """
        Args:
            node_id: This device's Meshtastic node ID.
            bridge: RadioBridge instance (must have send_text(text) → bool).
            interval: Seconds between heartbeat sends.
        """
        self.node_id = node_id
        self.bridge = bridge
        self.interval = interval
        self._last_sent: float = 0.0
        self._start_time: float = time.monotonic()
        self._send_count: int = 0

    def build_message(
        self,
        uptime_seconds: int,
        services: str = "",
        battery: int = -1,
    ) -> str:
        """Build a heartbeat wire-format message.

        Args:
            uptime_seconds: Agent uptime in seconds.
            services: Comma-separated service statuses (e.g., "edge:ok,mqtt:down").
            battery: Battery percentage (0-100) or -1 if unknown.

        Returns:
            Wire-format string: HEARTBEAT|{nodeId}|{uptime}|{services}|{battery}|{timestamp}
        """
        timestamp = datetime.utcnow().isoformat()
        return f"HEARTBEAT|{self.node_id}|{uptime_seconds}|{services}|{battery}|{timestamp}"

    def should_send(self) -> bool:
        """Check if enough time has elapsed since the last heartbeat."""
        return (time.monotonic() - self._last_sent) >= self.interval

    def send(
        self,
        uptime_seconds: int,
        services: str = "",
        battery: int = -1,
    ) -> bool:
        """Build and send a heartbeat message over the radio.

        Returns True if the message was sent successfully.
        """
        message = self.build_message(uptime_seconds, services, battery)
        try:
            result = self.bridge.send_text(message)  # type: ignore[attr-defined]
            if result:
                self._last_sent = time.monotonic()
                self._send_count += 1
                logger.info("Heartbeat sent: %s", message)
            else:
                logger.warning("Heartbeat send failed (bridge returned False)")
            return bool(result)
        except Exception as e:
            logger.error("Heartbeat send error: %s", e)
            return False

    def maybe_send(
        self,
        uptime_seconds: int,
        services: str = "",
        battery: int = -1,
    ) -> bool:
        """Send heartbeat only if the interval has elapsed.

        Designed to be called on every agent loop tick — it gates itself.
        Returns True if a heartbeat was sent, False if skipped or failed.
        """
        if not self.should_send():
            return False
        return self.send(uptime_seconds, services, battery)

    @property
    def send_count(self) -> int:
        """Total heartbeats sent since creation."""
        return self._send_count

    def build_services_from_health(self, health_report: object) -> str:
        """Build a services string from an AgentHealthReport.

        Args:
            health_report: An AgentHealthReport instance with radio_connected,
                          mqtt_connected fields.

        Returns:
            Services string like "edge:ok,radio:ok,mqtt:down"
        """
        parts: list[str] = []
        parts.append("edge:ok")  # If we're sending, the edge agent is running

        radio = getattr(health_report, "radio_connected", False)
        parts.append(f"radio:{'ok' if radio else 'down'}")

        mqtt = getattr(health_report, "mqtt_connected", False)
        parts.append(f"mqtt:{'ok' if mqtt else 'down'}")

        return ",".join(parts)
