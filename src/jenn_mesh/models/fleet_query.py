"""Fleet query models — natural language query plans and responses."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class QueryFunction(str, Enum):
    """Allowlisted high-level query functions for fleet NL queries.

    Each member maps to a validated dispatch method in FleetQueryEngine.
    The LLM can only call functions in this enum — no raw SQL.
    """

    FIND_DEVICES = "find_devices"
    GET_FLEET_SUMMARY = "get_fleet_summary"
    GET_ACTIVE_ALERTS = "get_active_alerts"
    GET_DEVICE_TELEMETRY = "get_device_telemetry"
    GET_MESH_TOPOLOGY = "get_mesh_topology"
    FIND_SPOF_NODES = "find_spof_nodes"
    GET_DEVICE_HISTORY = "get_device_history"
    GET_OFFLINE_TRANSITIONS = "get_offline_transitions"


class QueryStep(BaseModel):
    """A single step in a query plan."""

    function: QueryFunction
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="", description="LLM's explanation of this step")


class QueryPlan(BaseModel):
    """Structured plan parsed from natural language by Ollama."""

    steps: list[QueryStep] = Field(default_factory=list, max_length=3)
    reasoning: str = Field(default="", description="LLM's reasoning about the plan")


class FleetQueryRequest(BaseModel):
    """API request body for fleet queries."""

    question: str = Field(
        description="Natural language question about the fleet",
        min_length=3,
        max_length=500,
    )


class FleetQueryResponse(BaseModel):
    """Complete query response with answer and metadata."""

    question: str
    answer: str
    source: str = Field(description="How the answer was produced: ollama, keyword, or canned")
    query_plan: Optional[QueryPlan] = None
    raw_data: Optional[dict[str, Any]] = None
    duration_ms: int = 0
    ollama_available: bool = False


# Pre-built canned queries for fallback UI
CANNED_QUERIES: list[dict[str, str]] = [
    {"question": "Show all offline nodes", "description": "Devices not seen recently"},
    {"question": "Fleet health summary", "description": "Overall fleet status and counts"},
    {"question": "Active critical alerts", "description": "Unresolved critical alerts"},
    {"question": "Low battery devices", "description": "Nodes with battery below 20%"},
    {"question": "Network topology", "description": "Mesh graph and connectivity"},
    {
        "question": "Single points of failure",
        "description": "Nodes whose loss would partition the mesh",
    },
    {"question": "Config drift report", "description": "Devices drifted from golden templates"},
    {
        "question": "Recent offline transitions",
        "description": "Nodes that went offline in the last 24h",
    },
]
