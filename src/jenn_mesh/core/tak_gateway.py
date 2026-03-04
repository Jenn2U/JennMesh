"""TAK Gateway — bidirectional bridge between JennMesh and TAK ecosystem.

Translates Meshtastic position packets to CoT (Cursor on Target) XML events
and publishes to a TAK Server via TCP. Also ingests CoT events from TAK
Server and surfaces them as JennMesh alerts and waypoints.

Usage (production)::

    gateway = TakGateway(db=app.state.db)
    await gateway.connect()
    gateway.translate_position_to_cot(node_id, lat, lon, alt, battery)

Usage (standalone)::

    gateway = TakGateway(db=db)
    gateway.translate_position_to_cot("!2a3b4c", 32.123, -96.789, 100.0)
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from xml.etree.ElementTree import Element, SubElement, tostring

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.tak import (
    CotEvent,
    CotType,
    TakConnectionStatus,
    TakGatewayStatus,
    TakServerConfig,
)

logger = logging.getLogger(__name__)

# MQTT topic for TAK commands (dashboard → agent)
TAK_COMMAND_TOPIC = "jenn/mesh/command/tak"


class TakGateway:
    """Bidirectional gateway between JennMesh and TAK (Team Awareness Kit).

    Responsibilities:
    - Translate mesh node positions to CoT XML events
    - Publish CoT events to TAK Server via TCP/UDP
    - Log all events for audit trail
    - Provide gateway status for monitoring
    """

    def __init__(
        self,
        db: MeshDatabase,
        mqtt_client: Optional[Any] = None,
    ):
        self._db = db
        self._mqtt_client = mqtt_client
        self._status = TakGatewayStatus()
        self._config: Optional[TakServerConfig] = None
        self._load_config()

    def _load_config(self) -> None:
        """Load TAK config from DB if available."""
        row = self._db.get_tak_config()
        if row:
            self._config = TakServerConfig(
                host=row["host"],
                port=row["port"],
                use_tls=bool(row["use_tls"]),
                callsign_prefix=row["callsign_prefix"],
                stale_timeout_seconds=row["stale_timeout_seconds"],
                enabled=bool(row["enabled"]),
            )
            self._status.server_host = self._config.host
            self._status.server_port = self._config.port

    def configure(
        self,
        host: str,
        port: int = 8087,
        use_tls: bool = False,
        callsign_prefix: str = "JENN-",
        stale_timeout_seconds: int = 600,
        enabled: bool = True,
    ) -> TakServerConfig:
        """Update TAK server configuration."""
        self._db.upsert_tak_config(
            host=host,
            port=port,
            use_tls=use_tls,
            callsign_prefix=callsign_prefix,
            stale_timeout_seconds=stale_timeout_seconds,
            enabled=enabled,
        )
        self._config = TakServerConfig(
            host=host,
            port=port,
            use_tls=use_tls,
            callsign_prefix=callsign_prefix,
            stale_timeout_seconds=stale_timeout_seconds,
            enabled=enabled,
        )
        self._status.server_host = host
        self._status.server_port = port
        logger.info("TAK gateway configured: %s:%d (TLS=%s)", host, port, use_tls)
        return self._config

    def get_config(self) -> Optional[TakServerConfig]:
        """Return current TAK server configuration."""
        return self._config

    def get_status(self) -> TakGatewayStatus:
        """Return current gateway status."""
        counts = self._db.get_tak_event_counts()
        self._status.events_sent = counts.get("outbound", 0)
        self._status.events_received = counts.get("inbound", 0)
        return self._status

    def translate_position_to_cot(
        self,
        node_id: str,
        latitude: float,
        longitude: float,
        altitude: float = 0.0,
        battery: int | None = None,
        speed: float | None = None,
        course: float | None = None,
        cot_type: str = CotType.FRIENDLY_GROUND.value,
    ) -> CotEvent:
        """Translate a mesh node position to a CoT event.

        Args:
            node_id: Mesh radio node identifier.
            latitude: WGS84 latitude in degrees.
            longitude: WGS84 longitude in degrees.
            altitude: Altitude in meters HAE.
            battery: Battery percentage (0-100).
            speed: Speed in m/s.
            course: Heading in degrees.
            cot_type: CoT type code (default: friendly ground).

        Returns:
            CotEvent with populated fields.
        """
        prefix = self._config.callsign_prefix if self._config else "JENN-"
        stale_secs = self._config.stale_timeout_seconds if self._config else 600

        now = datetime.now(timezone.utc)
        callsign = f"{prefix}{node_id.lstrip('!')[:8]}"
        uid = f"JENN-MESH-{node_id.lstrip('!')}"

        event = CotEvent(
            uid=uid,
            cot_type=cot_type,
            callsign=callsign,
            latitude=latitude,
            longitude=longitude,
            altitude=altitude,
            battery=battery,
            speed=speed,
            course=course,
            time=now,
            start=now,
            stale=now + timedelta(seconds=stale_secs),
        )

        # Generate XML and log
        xml_str = self.cot_to_xml(event)
        self._db.log_tak_event(
            uid=uid,
            cot_type=cot_type,
            callsign=callsign,
            node_id=node_id,
            direction="outbound",
            latitude=latitude,
            longitude=longitude,
            altitude=altitude,
            raw_xml=xml_str,
        )

        self._status.events_sent += 1
        self._status.last_event_time = now
        logger.debug("CoT event generated for node %s: %s", node_id, callsign)

        return event

    @staticmethod
    def cot_to_xml(event: CotEvent) -> str:
        """Convert a CotEvent to CoT XML string.

        Follows the CoT XML schema used by ATAK/WinTAK/TAK Server.
        """
        time_fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
        now = event.time or datetime.now(timezone.utc)
        start = event.start or now
        stale = event.stale or (now + timedelta(seconds=600))

        root = Element("event")
        root.set("version", "2.0")
        root.set("uid", event.uid)
        root.set("type", event.cot_type)
        root.set("time", now.strftime(time_fmt))
        root.set("start", start.strftime(time_fmt))
        root.set("stale", stale.strftime(time_fmt))
        root.set("how", "m-g")  # machine-generated GPS

        point = SubElement(root, "point")
        point.set("lat", f"{event.latitude:.7f}")
        point.set("lon", f"{event.longitude:.7f}")
        point.set("hae", f"{event.altitude:.1f}")
        point.set("ce", f"{event.ce:.1f}")
        point.set("le", f"{event.le:.1f}")

        detail = SubElement(root, "detail")

        # Contact info
        contact = SubElement(detail, "contact")
        contact.set("callsign", event.callsign)

        # Remarks with metadata
        remarks = SubElement(detail, "remarks")
        remarks.text = event.remarks

        # Track info if available
        if event.speed is not None or event.course is not None:
            track = SubElement(detail, "__group")
            if event.speed is not None:
                track.set("speed", f"{event.speed:.1f}")
            if event.course is not None:
                track.set("course", f"{event.course:.1f}")

        # Status (battery)
        if event.battery is not None:
            status = SubElement(detail, "status")
            status.set("battery", str(event.battery))

        return tostring(root, encoding="unicode")

    @staticmethod
    def parse_cot_xml(xml_str: str) -> Optional[CotEvent]:
        """Parse a CoT XML string into a CotEvent model.

        Used for inbound CoT events from TAK Server.
        """
        try:
            from xml.etree.ElementTree import fromstring

            root = fromstring(xml_str)
            point = root.find("point")
            if point is None:
                return None

            detail = root.find("detail")
            contact = detail.find("contact") if detail is not None else None
            status_elem = detail.find("status") if detail is not None else None

            callsign = contact.get("callsign", "") if contact is not None else ""
            battery = None
            if status_elem is not None and status_elem.get("battery"):
                try:
                    battery = int(status_elem.get("battery", "0"))
                except ValueError:
                    pass

            return CotEvent(
                uid=root.get("uid", ""),
                cot_type=root.get("type", "a-u-G"),
                callsign=callsign,
                latitude=float(point.get("lat", "0")),
                longitude=float(point.get("lon", "0")),
                altitude=float(point.get("hae", "0")),
                ce=float(point.get("ce", "50")),
                le=float(point.get("le", "50")),
                battery=battery,
            )
        except Exception:
            logger.exception("Failed to parse CoT XML")
            return None

    def list_events(
        self,
        direction: str | None = None,
        node_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List TAK events from the DB."""
        return self._db.list_tak_events(direction=direction, node_id=node_id, limit=limit)
