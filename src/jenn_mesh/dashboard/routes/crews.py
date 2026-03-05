"""CrewAI crew execution API routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from jenn_mesh.crews.config import CREWAI_ENABLED

router = APIRouter(tags=["crews"])


class ProvisioningRequest(BaseModel):
    """Request body for provisioning crew."""

    terrain: str = Field("urban", description="Deployment terrain type")
    num_nodes: int = Field(3, ge=1, le=100, description="Number of nodes to deploy")
    power_source: str = Field("battery", description="Power source type")


class FleetQueryRequest(BaseModel):
    """Request body for fleet query crew."""

    question: str = Field(
        ..., min_length=3, max_length=500, description="Natural language question"
    )


def _disabled_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "CrewAI is not enabled",
            "detail": "Set CREWAI_ENABLED=true to activate crew orchestration.",
        },
    )


@router.get("/crews/status")
async def crews_status(request: Request) -> dict:
    """Get CrewAI availability and list of available crews."""
    from jenn_mesh.crews import available_crews

    return {
        "enabled": CREWAI_ENABLED,
        "crews": available_crews() if CREWAI_ENABLED else [],
    }


@router.post("/crews/fleet-health")
async def run_fleet_health(request: Request) -> JSONResponse:
    """Run fleet health analysis crew."""
    if not CREWAI_ENABLED:
        return _disabled_response()

    from jenn_mesh.crews import get_crew

    crew = get_crew("fleet_health")
    if crew is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Fleet health crew unavailable (crewai not installed?)"},
        )

    try:
        result = crew.kickoff()
        return JSONResponse(
            content={"crew": "fleet_health", "result": str(result), "status": "completed"}
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Crew execution failed: {type(exc).__name__}: {exc}"},
        )


@router.post("/crews/incident/{node_id}")
async def run_incident_response(request: Request, node_id: str) -> JSONResponse:
    """Run incident response crew for a specific node."""
    if not CREWAI_ENABLED:
        return _disabled_response()

    from jenn_mesh.crews import get_crew

    crew = get_crew("incident_response", node_id=node_id)
    if crew is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Incident response crew unavailable"},
        )

    try:
        result = crew.kickoff()
        return JSONResponse(
            content={
                "crew": "incident_response",
                "node_id": node_id,
                "result": str(result),
                "status": "completed",
            }
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Crew execution failed: {type(exc).__name__}: {exc}"},
        )


@router.post("/crews/provisioning")
async def run_provisioning(request: Request, body: ProvisioningRequest) -> JSONResponse:
    """Run provisioning advisory crew."""
    if not CREWAI_ENABLED:
        return _disabled_response()

    from jenn_mesh.crews import get_crew

    crew = get_crew(
        "provisioning",
        terrain=body.terrain,
        num_nodes=body.num_nodes,
        power_source=body.power_source,
    )
    if crew is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Provisioning crew unavailable"},
        )

    try:
        result = crew.kickoff()
        return JSONResponse(
            content={"crew": "provisioning", "result": str(result), "status": "completed"}
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Crew execution failed: {type(exc).__name__}: {exc}"},
        )


@router.post("/crews/query")
async def run_fleet_query(request: Request, body: FleetQueryRequest) -> JSONResponse:
    """Run fleet query crew for natural language question."""
    if not CREWAI_ENABLED:
        return _disabled_response()

    from jenn_mesh.crews import get_crew

    crew = get_crew("fleet_query", question=body.question)
    if crew is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Fleet query crew unavailable"},
        )

    try:
        result = crew.kickoff()
        return JSONResponse(
            content={
                "crew": "fleet_query",
                "question": body.question,
                "result": str(result),
                "status": "completed",
            }
        )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Crew execution failed: {type(exc).__name__}: {exc}"},
        )
