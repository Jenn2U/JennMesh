"""Alert summarization API routes — AI-powered alert collapse."""

from __future__ import annotations

from fastapi import APIRouter, Request

from jenn_mesh.core.alert_summarizer import AlertSummarizer

router = APIRouter(tags=["alert_summary"])


def _get_summarizer(request: Request) -> AlertSummarizer:
    """Get or create an AlertSummarizer from request state."""
    summarizer = getattr(request.app.state, "alert_summarizer", None)
    if summarizer is not None:
        return summarizer
    # Fallback: create one without Ollama
    db = request.app.state.db
    return AlertSummarizer(db)


@router.get("/alerts/summary")
async def fleet_alert_summary(request: Request) -> dict:
    """Get AI-generated fleet-wide alert summary.

    Collapses all active alerts into a human-readable paragraph.
    Uses Ollama if available, otherwise rule-based fallback.
    """
    summarizer = _get_summarizer(request)
    return await summarizer.summarize_active()


@router.get("/alerts/summary/status")
async def summarizer_status(request: Request) -> dict:
    """Get alert summarizer availability and stats."""
    summarizer = _get_summarizer(request)
    return summarizer.get_status()


@router.get("/alerts/summary/{node_id}")
async def node_alert_summary(request: Request, node_id: str) -> dict:
    """Get AI-generated alert summary for a specific node."""
    summarizer = _get_summarizer(request)
    return await summarizer.summarize_for_node(node_id)
