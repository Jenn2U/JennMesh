"""Provisioning advisor API routes — AI-powered deployment recommendations."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from jenn_mesh.core.provisioning_advisor import ProvisioningAdvisor

router = APIRouter(tags=["provisioning_advisor"])


class DeploymentContext(BaseModel):
    """Request body for provisioning advice."""

    terrain: str = Field(default="urban", description="Terrain type")
    num_nodes: int = Field(default=3, ge=1, le=100)
    power_source: str = Field(default="battery", description="Power source type")
    desired_coverage_m: float = Field(default=5000.0, ge=0)
    existing_nodes: list[str] = Field(default_factory=list)


def _get_advisor(request: Request) -> ProvisioningAdvisor:
    """Get or create a ProvisioningAdvisor from request state."""
    advisor = getattr(request.app.state, "provisioning_advisor", None)
    if advisor is not None:
        return advisor
    db = request.app.state.db
    return ProvisioningAdvisor(db)


@router.post("/advisor/recommend")
async def recommend_deployment(request: Request, ctx: DeploymentContext) -> dict:
    """Generate AI-powered deployment recommendations.

    Accepts deployment context and returns role assignments, power
    settings, channel config, and deployment order.
    """
    advisor = _get_advisor(request)
    return await advisor.recommend(ctx.model_dump())


@router.get("/advisor/status")
async def advisor_status(request: Request) -> dict:
    """Get provisioning advisor availability."""
    advisor = _get_advisor(request)
    return advisor.get_status()
