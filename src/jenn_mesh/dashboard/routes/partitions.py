"""Network partition detection API endpoints.

Provides partition status, event history, and individual event details.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["topology"])


def _get_detector(request: Request):
    """Get or lazily create PartitionDetector."""
    detector = getattr(request.app.state, "partition_detector", None)
    if detector is not None:
        return detector
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from jenn_mesh.core.partition_detector import PartitionDetector

    detector = PartitionDetector(db=db)
    request.app.state.partition_detector = detector
    return detector


@router.get("/partitions/status")
async def partition_status(request: Request) -> dict:
    """Get current partition status with live topology analysis."""
    detector = _get_detector(request)
    return detector.get_partition_status()


@router.get("/partitions/events")
async def list_partition_events(
    request: Request, limit: int = 50, event_type: str | None = None
) -> dict:
    """List partition events with optional type filter."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    events = db.list_partition_events(limit=limit, event_type=event_type)
    # Parse JSON fields for response
    for event in events:
        if isinstance(event.get("components_json"), str):
            try:
                event["components"] = json.loads(event["components_json"])
            except (json.JSONDecodeError, TypeError):
                event["components"] = []
    return {"events": events, "count": len(events)}


@router.get("/partitions/events/{event_id}")
async def get_partition_event(request: Request, event_id: int) -> dict:
    """Get a single partition event by ID."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    event = db.get_partition_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Partition event not found")
    if isinstance(event.get("components_json"), str):
        try:
            event["components"] = json.loads(event["components_json"])
        except (json.JSONDecodeError, TypeError):
            event["components"] = []
    return event
