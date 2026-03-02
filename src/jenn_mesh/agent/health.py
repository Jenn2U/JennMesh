"""Agent health reporting — periodic heartbeat to dashboard API."""

from __future__ import annotations

import logging
import platform
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AgentHealthReport(BaseModel):
    """Health report sent from agent to dashboard."""

    agent_id: str = Field(description="Unique agent identifier (hostname or custom)")
    radio_connected: bool = Field(default=False)
    radio_port: Optional[str] = Field(default=None)
    mqtt_connected: bool = Field(default=False)
    packets_received: int = Field(default=0, description="Total packets since start")
    packets_forwarded: int = Field(default=0, description="Packets forwarded to MQTT")
    uptime_seconds: float = Field(default=0)
    hostname: str = Field(default_factory=platform.node)
    platform: str = Field(default_factory=platform.system)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AgentHealthMonitor:
    """Tracks agent health metrics and reports to the dashboard API."""

    def __init__(self, agent_id: Optional[str] = None):
        self.agent_id = agent_id or platform.node()
        self._start_time = datetime.utcnow()
        self._packets_received = 0
        self._packets_forwarded = 0
        self._radio_connected = False
        self._mqtt_connected = False
        self._radio_port: Optional[str] = None

    def record_packet_received(self) -> None:
        """Increment received packet counter."""
        self._packets_received += 1

    def record_packet_forwarded(self) -> None:
        """Increment forwarded packet counter."""
        self._packets_forwarded += 1

    def set_radio_status(self, connected: bool, port: Optional[str] = None) -> None:
        """Update radio connection status."""
        self._radio_connected = connected
        self._radio_port = port

    def set_mqtt_status(self, connected: bool) -> None:
        """Update MQTT connection status."""
        self._mqtt_connected = connected

    def get_report(self) -> AgentHealthReport:
        """Generate current health report."""
        uptime = (datetime.utcnow() - self._start_time).total_seconds()
        return AgentHealthReport(
            agent_id=self.agent_id,
            radio_connected=self._radio_connected,
            radio_port=self._radio_port,
            mqtt_connected=self._mqtt_connected,
            packets_received=self._packets_received,
            packets_forwarded=self._packets_forwarded,
            uptime_seconds=uptime,
        )

    async def report_to_dashboard(self, dashboard_url: str) -> bool:
        """Send health report to the dashboard API."""
        try:
            import httpx

            report = self.get_report()
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{dashboard_url}/api/v1/agent/health",
                    json=report.model_dump(mode="json"),
                    timeout=10,
                )
                return resp.status_code == 200
        except Exception as e:
            logger.warning("Failed to report health: %s", e)
            return False
