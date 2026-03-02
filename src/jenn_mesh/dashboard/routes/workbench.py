"""Workbench API routes — single-radio config builder + bulk push."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from jenn_mesh.models.workbench import (
    ApplyRequest,
    BulkPushRequest,
    ConnectionRequest,
    SaveTemplateRequest,
)

router = APIRouter(tags=["workbench"])


# ── Workbench (single radio) ────────────────────────────────────────


@router.post("/workbench/connect")
async def workbench_connect(request: Request, body: ConnectionRequest) -> dict:
    """Connect the workbench to a radio."""
    wm = request.app.state.workbench
    status = await asyncio.to_thread(wm.connect, body)
    return status.model_dump()


@router.post("/workbench/disconnect")
async def workbench_disconnect(request: Request) -> dict:
    """Disconnect the current workbench radio."""
    wm = request.app.state.workbench
    status = await asyncio.to_thread(wm.disconnect)
    return status.model_dump()


@router.get("/workbench/status")
async def workbench_status(request: Request) -> dict:
    """Get current workbench connection status and radio info."""
    wm = request.app.state.workbench
    status = wm.get_status()
    return status.model_dump()


@router.get("/workbench/config")
async def workbench_config(request: Request) -> dict:
    """Read full structured config from the connected radio."""
    wm = request.app.state.workbench
    try:
        config = await asyncio.to_thread(wm.read_config)
        return config.model_dump()
    except RuntimeError as e:
        return {"error": str(e)}


@router.post("/workbench/diff")
async def workbench_diff(request: Request, body: ApplyRequest) -> dict:
    """Preview config changes (diff) against the current radio config."""
    wm = request.app.state.workbench
    try:
        diff = wm.compute_diff(body.sections)
        return diff.model_dump()
    except RuntimeError as e:
        return {"error": str(e)}


@router.post("/workbench/apply")
async def workbench_apply(request: Request, body: ApplyRequest) -> dict:
    """Apply edited config sections to the connected radio."""
    wm = request.app.state.workbench
    result = await asyncio.to_thread(wm.apply_config, body.sections)
    return result.model_dump()


@router.post("/workbench/save-template")
async def workbench_save_template(request: Request, body: SaveTemplateRequest) -> dict:
    """Save the current radio config as a new golden template."""
    wm = request.app.state.workbench
    result = await asyncio.to_thread(wm.save_as_template, body)
    return result.model_dump()


# ── Bulk Push ────────────────────────────────────────────────────────


@router.post("/config/push")
async def config_push(request: Request, body: BulkPushRequest) -> dict:
    """Start a bulk push of a template to multiple fleet devices."""
    bpm = request.app.state.bulk_push
    try:
        progress = bpm.start_push(body)
        return progress.model_dump()
    except ValueError as e:
        return {"error": str(e)}


@router.get("/config/push/{push_id}")
async def config_push_progress(request: Request, push_id: str) -> dict:
    """Get progress of a bulk push operation."""
    bpm = request.app.state.bulk_push
    progress = bpm.get_progress(push_id)
    if progress is None:
        return {"error": f"Push '{push_id}' not found"}
    return progress.model_dump()
