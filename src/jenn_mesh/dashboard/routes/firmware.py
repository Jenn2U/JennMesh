"""Firmware API routes — compatibility matrix, fleet firmware status."""

from __future__ import annotations

from fastapi import APIRouter, Request

from jenn_mesh.provisioning.firmware import FirmwareTracker

router = APIRouter(tags=["firmware"])


@router.get("/firmware/status")
async def fleet_firmware_status(request: Request) -> dict:
    """Get firmware status for all devices in the fleet."""
    db = request.app.state.db
    tracker = FirmwareTracker(db)
    report = tracker.get_fleet_firmware_report()

    return {"count": len(report), "devices": report}


@router.get("/firmware/status/{node_id}")
async def device_firmware_status(request: Request, node_id: str) -> dict:
    """Get firmware status for a specific device."""
    db = request.app.state.db
    tracker = FirmwareTracker(db)
    status = tracker.check_device_firmware(node_id)

    if status is None:
        return {"error": "Device not found", "node_id": node_id}

    return status


@router.get("/firmware/compatibility")
async def compatibility_matrix(request: Request) -> dict:
    """Get the full firmware-hardware compatibility matrix."""
    db = request.app.state.db
    tracker = FirmwareTracker(db)
    matrix = tracker.get_compatibility_matrix()

    return {"count": len(matrix), "entries": matrix}


@router.get("/firmware/compatibility/{hw_model}")
async def compatible_firmware(request: Request, hw_model: str) -> dict:
    """Get compatible firmware versions for a specific hardware model."""
    db = request.app.state.db
    tracker = FirmwareTracker(db)
    versions = tracker.get_compatible_versions(hw_model)

    return {"hw_model": hw_model, "count": len(versions), "versions": versions}


@router.get("/firmware/upgradeable")
async def upgradeable_devices(request: Request) -> dict:
    """Get devices that can safely be upgraded to latest firmware."""
    db = request.app.state.db
    tracker = FirmwareTracker(db)
    devices = tracker.get_upgradeable_devices()

    return {"count": len(devices), "devices": devices}
