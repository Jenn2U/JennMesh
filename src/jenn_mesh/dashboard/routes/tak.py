"""TAK gateway API endpoints.

Provides configuration, CoT event translation, event history, and gateway status.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tak"])


def _get_gateway(request: Request):
    """Get or lazily create TakGateway."""
    gateway = getattr(request.app.state, "tak_gateway", None)
    if gateway is not None:
        return gateway
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from jenn_mesh.core.tak_gateway import TakGateway

    gateway = TakGateway(db=db)
    request.app.state.tak_gateway = gateway
    return gateway


# ── Request/Response models ───────────────────────────────────────────


class TakConfigRequest(BaseModel):
    host: str = Field(description="TAK server hostname or IP")
    port: int = Field(default=8087, description="TAK server port")
    use_tls: bool = Field(default=False, description="Use TLS")
    callsign_prefix: str = Field(default="JENN-", description="Node callsign prefix")
    stale_timeout_seconds: int = Field(default=600, description="CoT marker stale timeout")
    enabled: bool = Field(default=True)


class TranslatePositionRequest(BaseModel):
    node_id: str = Field(description="Mesh radio node_id")
    latitude: float = Field(description="WGS84 latitude")
    longitude: float = Field(description="WGS84 longitude")
    altitude: float = Field(default=0.0, description="Altitude in meters HAE")
    battery: int | None = Field(default=None, description="Battery percentage")
    speed: float | None = Field(default=None, description="Speed in m/s")
    course: float | None = Field(default=None, description="Heading degrees")
    cot_type: str = Field(default="a-f-G", description="CoT type code")


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/tak/status")
async def tak_status(request: Request) -> dict:
    """Get TAK gateway status."""
    gateway = _get_gateway(request)
    status = gateway.get_status()
    return {
        "status": "ok",
        "gateway": {
            "connection_status": status.connection_status.value,
            "server_host": status.server_host,
            "server_port": status.server_port,
            "events_sent": status.events_sent,
            "events_received": status.events_received,
            "last_event_time": (
                status.last_event_time.isoformat() if status.last_event_time else None
            ),
            "tracked_nodes": status.tracked_nodes,
            "errors": status.errors,
        },
    }


@router.get("/tak/config")
async def get_tak_config(request: Request) -> dict:
    """Get current TAK server configuration."""
    gateway = _get_gateway(request)
    config = gateway.get_config()
    if config is None:
        return {"status": "ok", "config": None, "message": "No TAK server configured"}
    return {
        "status": "ok",
        "config": {
            "host": config.host,
            "port": config.port,
            "use_tls": config.use_tls,
            "callsign_prefix": config.callsign_prefix,
            "stale_timeout_seconds": config.stale_timeout_seconds,
            "enabled": config.enabled,
        },
    }


@router.post("/tak/config")
async def update_tak_config(request: Request, body: TakConfigRequest) -> dict:
    """Update TAK server configuration."""
    gateway = _get_gateway(request)
    config = gateway.configure(
        host=body.host,
        port=body.port,
        use_tls=body.use_tls,
        callsign_prefix=body.callsign_prefix,
        stale_timeout_seconds=body.stale_timeout_seconds,
        enabled=body.enabled,
    )
    return {
        "status": "ok",
        "config": {
            "host": config.host,
            "port": config.port,
            "use_tls": config.use_tls,
            "callsign_prefix": config.callsign_prefix,
            "stale_timeout_seconds": config.stale_timeout_seconds,
            "enabled": config.enabled,
        },
    }


@router.post("/tak/translate")
async def translate_position(request: Request, body: TranslatePositionRequest) -> dict:
    """Translate a mesh node position to CoT XML."""
    gateway = _get_gateway(request)
    event = gateway.translate_position_to_cot(
        node_id=body.node_id,
        latitude=body.latitude,
        longitude=body.longitude,
        altitude=body.altitude,
        battery=body.battery,
        speed=body.speed,
        course=body.course,
        cot_type=body.cot_type,
    )
    xml = gateway.cot_to_xml(event)
    return {
        "status": "ok",
        "event": {
            "uid": event.uid,
            "callsign": event.callsign,
            "cot_type": event.cot_type,
            "latitude": event.latitude,
            "longitude": event.longitude,
            "altitude": event.altitude,
        },
        "xml": xml,
    }


@router.get("/tak/events")
async def list_tak_events(
    request: Request,
    direction: str | None = None,
    node_id: str | None = None,
    limit: int = 50,
) -> dict:
    """List TAK CoT events."""
    gateway = _get_gateway(request)
    events = gateway.list_events(direction=direction, node_id=node_id, limit=limit)
    return {"status": "ok", "count": len(events), "events": events}


@router.post("/tak/parse")
async def parse_cot_xml(request: Request) -> dict:
    """Parse CoT XML into a structured event (for testing/debugging)."""
    from jenn_mesh.core.tak_gateway import TakGateway

    body = await request.body()
    xml_str = body.decode("utf-8")
    if not xml_str.strip():
        raise HTTPException(status_code=400, detail="Empty XML body")

    event = TakGateway.parse_cot_xml(xml_str)
    if event is None:
        raise HTTPException(status_code=400, detail="Failed to parse CoT XML")

    return {
        "status": "ok",
        "event": {
            "uid": event.uid,
            "callsign": event.callsign,
            "cot_type": event.cot_type,
            "latitude": event.latitude,
            "longitude": event.longitude,
            "altitude": event.altitude,
            "battery": event.battery,
        },
    }
