"""Health scoring API routes — per-node and fleet health scores (MESH-022)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from jenn_mesh.core.health_scoring import HealthScorer

router = APIRouter(tags=["health-scoring"])


@router.get("/health/scores")
async def fleet_health_scores(request: Request) -> dict:
    """Get health scores for all devices in the fleet."""
    db = request.app.state.db
    scorer = HealthScorer(db)
    scores = scorer.score_fleet()
    return {
        "count": len(scores),
        "scores": [s.model_dump() for s in scores],
    }


@router.get("/health/scores/{node_id}")
async def device_health_score(request: Request, node_id: str) -> dict:
    """Get detailed health score breakdown for a specific device."""
    db = request.app.state.db
    scorer = HealthScorer(db)
    result = scorer.score_device(node_id)
    if result is None:
        return {"error": "Device not found", "node_id": node_id}
    return result.model_dump()


@router.get("/health/summary")
async def fleet_health_summary(request: Request) -> dict:
    """Get aggregate fleet health statistics."""
    db = request.app.state.db
    scorer = HealthScorer(db)
    return scorer.fleet_summary()
