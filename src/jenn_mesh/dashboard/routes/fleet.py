"""Fleet API routes — device list, status, and health."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from jenn_mesh.core.health_scoring import HealthScorer
from jenn_mesh.core.registry import DeviceRegistry

router = APIRouter(tags=["fleet"])


@router.get("/fleet")
async def list_fleet(request: Request) -> dict:
    """List all devices in the fleet with current status."""
    db = request.app.state.db
    registry = DeviceRegistry(db)
    devices = registry.list_devices()

    # Compute health scores for all devices
    scorer = HealthScorer(db)
    score_map: dict[str, tuple] = {}
    for d in devices:
        result = scorer.score_device(d.node_id)
        if result:
            score_map[d.node_id] = (result.overall_score, result.grade.value)

    return {
        "count": len(devices),
        "devices": [
            {
                "node_id": d.node_id,
                "long_name": d.long_name,
                "short_name": d.short_name,
                "role": d.role.value,
                "hardware": d.firmware.hw_model,
                "firmware": d.firmware.version,
                "battery_level": d.battery_level,
                "signal_snr": d.signal_snr,
                "signal_rssi": d.signal_rssi,
                "is_online": d.is_online,
                "last_seen": d.last_seen.isoformat() if d.last_seen else None,
                "latitude": d.latitude,
                "longitude": d.longitude,
                "mesh_status": d.mesh_status,
                "last_mesh_heartbeat": (
                    d.last_mesh_heartbeat.isoformat() if d.last_mesh_heartbeat else None
                ),
                "health_score": score_map.get(d.node_id, (None, None))[0],
                "health_grade": score_map.get(d.node_id, (None, None))[1],
            }
            for d in devices
        ],
    }


@router.get("/fleet/health")
async def fleet_health(request: Request) -> dict:
    """Get aggregate fleet health statistics."""
    db = request.app.state.db
    registry = DeviceRegistry(db)
    health = registry.get_fleet_health()

    return {
        "total_devices": health.total_devices,
        "online_count": health.online_count,
        "offline_count": health.offline_count,
        "degraded_count": health.degraded_count,
        "health_score": health.health_score,
        "active_alerts": health.active_alerts,
        "critical_alerts": health.critical_alerts,
        "devices_needing_update": health.devices_needing_update,
        "devices_with_drift": health.devices_with_drift,
        "mesh_reachable_count": health.mesh_reachable_count,
    }


@router.get("/fleet/{node_id}")
async def get_device(request: Request, node_id: str) -> dict:
    """Get details for a specific device."""
    db = request.app.state.db
    registry = DeviceRegistry(db)
    device = registry.get_device(node_id)

    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    return {
        "node_id": device.node_id,
        "long_name": device.long_name,
        "short_name": device.short_name,
        "role": device.role.value,
        "firmware": {
            "version": device.firmware.version,
            "hw_model": device.firmware.hw_model,
            "needs_update": device.firmware.needs_update,
        },
        "battery_level": device.battery_level,
        "voltage": device.voltage,
        "signal_snr": device.signal_snr,
        "signal_rssi": device.signal_rssi,
        "is_online": device.is_online,
        "last_seen": device.last_seen.isoformat() if device.last_seen else None,
        "latitude": device.latitude,
        "longitude": device.longitude,
        "altitude": device.altitude,
        "associated_edge_node": device.associated_edge_node,
        "mesh_status": device.mesh_status,
        "last_mesh_heartbeat": (
            device.last_mesh_heartbeat.isoformat() if device.last_mesh_heartbeat else None
        ),
    }


@router.get("/fleet/alerts/active")
async def active_alerts(request: Request, node_id: Optional[str] = Query(None)) -> dict:
    """Get active alerts, optionally filtered by node."""
    db = request.app.state.db
    alerts = db.get_active_alerts(node_id)
    return {"count": len(alerts), "alerts": alerts}
