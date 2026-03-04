"""Lost node AI reasoning API routes — extends the existing locator."""

from __future__ import annotations

from fastapi import APIRouter, Request

from jenn_mesh.core.lost_node_reasoner import LostNodeReasoner

router = APIRouter(tags=["lost_node_ai"])


def _get_reasoner(request: Request) -> LostNodeReasoner:
    """Get or create a LostNodeReasoner from request state."""
    reasoner = getattr(request.app.state, "lost_node_reasoner", None)
    if reasoner is not None:
        return reasoner
    db = request.app.state.db
    return LostNodeReasoner(db)


@router.get("/locate/{node_id}/ai-reasoning")
async def ai_reasoning(request: Request, node_id: str) -> dict:
    """Get AI-powered reasoning about a lost node's probable location.

    Extends the existing /locate/{node_id} with probabilistic analysis.
    """
    reasoner = _get_reasoner(request)
    return await reasoner.reason(node_id)


@router.get("/locate/ai/status")
async def reasoner_status(request: Request) -> dict:
    """Get lost node AI reasoner availability."""
    reasoner = _get_reasoner(request)
    return reasoner.get_status()
