"""Heartbeat receiver — parses mesh heartbeats and updates device status."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.heartbeat import MeshHeartbeat

logger = logging.getLogger(__name__)

# Wire format: HEARTBEAT|{nodeId}|{uptime_s}|{services}|{battery}|{timestamp}
HEARTBEAT_PREFIX = "HEARTBEAT|"

# Device is considered mesh-unreachable after this many seconds without heartbeat
DEFAULT_STALE_THRESHOLD_SECONDS = 600  # 10 minutes (5x the 120s interval)


class HeartbeatReceiver:
    """Parses incoming mesh heartbeat text messages and stores them in the DB.

    Responsibilities:
        - Parse HEARTBEAT| wire format into MeshHeartbeat model
        - Store heartbeat in mesh_heartbeats table
        - Update device's mesh_status to 'reachable'
        - Detect stale heartbeats and flip devices to 'unreachable'
    """

    def __init__(
        self,
        db: MeshDatabase,
        stale_threshold_seconds: int = DEFAULT_STALE_THRESHOLD_SECONDS,
    ):
        self.db = db
        self.stale_threshold = timedelta(seconds=stale_threshold_seconds)

    def parse_heartbeat(
        self,
        text: str,
        rssi: Optional[int] = None,
        snr: Optional[float] = None,
    ) -> Optional[MeshHeartbeat]:
        """Parse a HEARTBEAT| wire-format text message into a MeshHeartbeat.

        Wire format: HEARTBEAT|{nodeId}|{uptime_s}|{services}|{battery}|{timestamp}

        Returns None if the message isn't a valid heartbeat.
        """
        if not text.startswith(HEARTBEAT_PREFIX):
            return None

        parts = text[len(HEARTBEAT_PREFIX) :].split("|")
        if len(parts) < 5:
            logger.warning("Malformed heartbeat (expected 5 fields, got %d): %s", len(parts), text)
            return None

        try:
            node_id = parts[0]
            uptime_seconds = int(parts[1])
            services = MeshHeartbeat.parse_services_string(parts[2])
            battery = int(parts[3])
            timestamp = datetime.fromisoformat(parts[4])

            return MeshHeartbeat(
                node_id=node_id,
                uptime_seconds=uptime_seconds,
                services=services,
                battery=battery,
                timestamp=timestamp,
                received_at=datetime.utcnow(),
                rssi=rssi,
                snr=snr,
            )
        except (ValueError, IndexError) as e:
            logger.warning("Failed to parse heartbeat: %s — %s", text, e)
            return None

    def process_heartbeat(self, heartbeat: MeshHeartbeat) -> None:
        """Store a parsed heartbeat in the DB and update the device's mesh status."""
        self.db.add_heartbeat(
            node_id=heartbeat.node_id,
            uptime_seconds=heartbeat.uptime_seconds,
            services_json=heartbeat.services_json(),
            battery=heartbeat.battery,
            rssi=heartbeat.rssi,
            snr=heartbeat.snr,
            timestamp=heartbeat.timestamp.isoformat(),
        )
        logger.info(
            "Heartbeat processed: %s uptime=%ds battery=%d%% services=%s",
            heartbeat.node_id,
            heartbeat.uptime_seconds,
            heartbeat.battery,
            MeshHeartbeat.format_services_string(heartbeat.services),
        )

    def handle_text_message(
        self,
        text: str,
        rssi: Optional[int] = None,
        snr: Optional[float] = None,
    ) -> bool:
        """Convenience: parse + process a raw text message. Returns True if handled."""
        heartbeat = self.parse_heartbeat(text, rssi=rssi, snr=snr)
        if heartbeat is None:
            return False
        self.process_heartbeat(heartbeat)
        return True

    def check_stale_heartbeats(self) -> list[str]:
        """Check for devices whose mesh heartbeat has gone stale.

        Flips mesh_status from 'reachable' → 'unreachable' for devices
        whose last_mesh_heartbeat is older than the stale threshold.

        Returns list of node_ids that were marked unreachable.
        """
        stale_nodes: list[str] = []
        cutoff = datetime.utcnow() - self.stale_threshold

        devices = self.db.list_devices()
        for device in devices:
            mesh_status = device.get("mesh_status", "unknown")
            last_hb = device.get("last_mesh_heartbeat")

            if mesh_status != "reachable":
                continue

            if last_hb is None:
                # Shouldn't be reachable without a heartbeat, fix it
                self.db.upsert_device(device["node_id"], mesh_status="unreachable")
                stale_nodes.append(device["node_id"])
                continue

            try:
                hb_time = datetime.fromisoformat(last_hb)
            except (ValueError, TypeError):
                continue

            if hb_time < cutoff:
                self.db.upsert_device(device["node_id"], mesh_status="unreachable")
                stale_nodes.append(device["node_id"])
                logger.info("Mesh heartbeat stale for %s, marking unreachable", device["node_id"])

        return stale_nodes
