"""Fleet NL query API routes — natural language fleet questions via Ollama."""

from __future__ import annotations

from fastapi import APIRouter, Request

from jenn_mesh.core.fleet_query_engine import FleetQueryEngine
from jenn_mesh.models.fleet_query import (
    CANNED_QUERIES,
    FleetQueryRequest,
    FleetQueryResponse,
)

router = APIRouter(tags=["fleet-query"])


def _get_engine(request: Request) -> FleetQueryEngine:
    """Get or create a FleetQueryEngine from request state."""
    engine = getattr(request.app.state, "fleet_query_engine", None)
    if engine is not None:
        return engine
    db = request.app.state.db
    return FleetQueryEngine(db)


@router.post("/fleet-query/ask", response_model=FleetQueryResponse)
async def ask_fleet_query(request: Request, body: FleetQueryRequest) -> FleetQueryResponse:
    """Submit a natural language question about the fleet.

    Uses Ollama (if available) to parse the question into a structured
    query plan, execute it, and format a conversational answer.
    Falls back to keyword matching or canned queries when Ollama is unavailable.
    """
    engine = _get_engine(request)
    return await engine.ask(body.question)


@router.get("/fleet-query/status")
async def fleet_query_status(request: Request) -> dict:
    """Get fleet query engine availability and Ollama status."""
    engine = _get_engine(request)
    return engine.get_status()


@router.get("/fleet-query/history")
async def fleet_query_history(request: Request, limit: int = 20) -> list[dict]:
    """Get recent NL query history."""
    engine = _get_engine(request)
    return engine.get_history(limit=limit)


@router.get("/fleet-query/canned")
async def fleet_query_canned() -> list[dict]:
    """Get pre-built canned query suggestions for the UI."""
    return CANNED_QUERIES
