"""Baseline API routes — per-node performance baselines and deviation detection."""

from __future__ import annotations

from fastapi import APIRouter, Request

from jenn_mesh.core.baselines import BaselineManager

router = APIRouter(tags=["baselines"])


@router.get("/baselines")
async def list_baselines(request: Request) -> dict:
    """Get all device baselines."""
    db = request.app.state.db
    manager = BaselineManager(db)
    baselines = manager.get_all_baselines()
    return {
        "count": len(baselines),
        "baselines": [b.model_dump() for b in baselines],
    }


@router.get("/baselines/deviations")
async def fleet_deviations(request: Request) -> dict:
    """Fleet-wide deviation scan — returns only degraded nodes."""
    db = request.app.state.db
    manager = BaselineManager(db)
    deviations = manager.check_fleet_deviations()
    return {
        "count": len(deviations),
        "deviations": [d.model_dump() for d in deviations],
    }


@router.get("/baselines/{node_id}")
async def get_baseline(request: Request, node_id: str) -> dict:
    """Get the baseline for a specific node."""
    db = request.app.state.db
    manager = BaselineManager(db)
    baseline = manager.get_baseline(node_id)
    if baseline is None:
        return {"error": "No baseline found", "node_id": node_id}
    return baseline.model_dump()


@router.get("/baselines/{node_id}/deviations")
async def node_deviations(request: Request, node_id: str) -> dict:
    """Check a specific node for baseline deviations."""
    db = request.app.state.db
    manager = BaselineManager(db)
    report = manager.check_deviation(node_id)
    if report is None:
        return {"error": "No baseline or device not found", "node_id": node_id}
    return report.model_dump()
