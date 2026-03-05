"""CrewAI agent orchestration for JennMesh fleet management.

Wraps existing AI services (AnomalyDetector, AlertSummarizer,
ProvisioningAdvisor, LostNodeReasoner, FleetQueryEngine) as
CrewAI tools and composes them into multi-agent crews.

No-op safe: when CREWAI_ENABLED is unset or false, all functions
return None and no CrewAI imports occur.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from jenn_mesh.crews.config import CREWAI_ENABLED

logger = logging.getLogger(__name__)

# Tracks whether init_crews() has been called
_initialized = False


def init_crews(app: Any) -> bool:
    """Initialize CrewAI tools with app.state services.

    Must be called after all services are registered on app.state.
    Returns True if initialization succeeded, False otherwise.
    """
    global _initialized

    if _initialized:
        return False

    if not CREWAI_ENABLED:
        logger.info("CrewAI disabled (CREWAI_ENABLED not set)")
        _initialized = True
        return False

    try:
        from jenn_mesh.crews.tools import set_services
    except ImportError:
        logger.warning("CrewAI tools module import failed")
        _initialized = True
        return False

    services = {
        "anomaly_detector": getattr(app.state, "anomaly_detector", None),
        "alert_summarizer": getattr(app.state, "alert_summarizer", None),
        "provisioning_advisor": getattr(app.state, "provisioning_advisor", None),
        "lost_node_reasoner": getattr(app.state, "lost_node_reasoner", None),
        "fleet_query_engine": getattr(app.state, "fleet_query_engine", None),
    }

    set_services(services)
    logger.info("CrewAI tools initialized with %d services", len(services))
    _initialized = True
    return True


def get_crew(crew_name: str, **kwargs: Any) -> Optional[Any]:
    """Get a configured crew by name.

    Args:
        crew_name: One of 'fleet_health', 'incident_response',
                   'provisioning', 'fleet_query'.
        **kwargs: Crew-specific parameters (e.g. node_id, context, question).

    Returns CrewAI Crew instance, or None if disabled/unavailable.
    """
    if not CREWAI_ENABLED:
        return None

    try:
        from jenn_mesh.crews.tools import ALL_TOOLS
    except ImportError:
        return None

    creators = {
        "fleet_health": _create_fleet_health,
        "incident_response": _create_incident_response,
        "provisioning": _create_provisioning,
        "fleet_query": _create_fleet_query,
    }

    creator = creators.get(crew_name)
    if creator is None:
        logger.warning("Unknown crew name: %s", crew_name)
        return None

    return creator(ALL_TOOLS, **kwargs)


def available_crews() -> list[dict[str, str]]:
    """List available crew types with descriptions."""
    return [
        {
            "name": "fleet_health",
            "description": "Fleet-wide health analysis with anomaly detection and alert summary",
        },
        {
            "name": "incident_response",
            "description": "Node-specific incident investigation and recovery planning",
            "params": "node_id (required)",
        },
        {
            "name": "provisioning",
            "description": "Deployment planning for new mesh nodes",
            "params": "terrain, num_nodes, power_source",
        },
        {
            "name": "fleet_query",
            "description": "Natural language fleet queries with multi-agent analysis",
            "params": "question (required)",
        },
    ]


# ── Private creators ─────────────────────────────────────────────────


def _create_fleet_health(tools: dict[str, Any], **kwargs: Any) -> Optional[Any]:
    from jenn_mesh.crews.fleet_health import create_fleet_health_crew

    return create_fleet_health_crew(tools)


def _create_incident_response(tools: dict[str, Any], **kwargs: Any) -> Optional[Any]:
    node_id = kwargs.get("node_id")
    if not node_id:
        logger.warning("incident_response crew requires node_id parameter")
        return None
    from jenn_mesh.crews.incident_response import create_incident_response_crew

    return create_incident_response_crew(tools, node_id)


def _create_provisioning(tools: dict[str, Any], **kwargs: Any) -> Optional[Any]:
    context = {
        "terrain": kwargs.get("terrain", "urban"),
        "num_nodes": kwargs.get("num_nodes", 3),
        "power_source": kwargs.get("power_source", "battery"),
    }
    from jenn_mesh.crews.provisioning import create_provisioning_crew

    return create_provisioning_crew(tools, context)


def _create_fleet_query(tools: dict[str, Any], **kwargs: Any) -> Optional[Any]:
    question = kwargs.get("question")
    if not question:
        logger.warning("fleet_query crew requires question parameter")
        return None
    from jenn_mesh.crews.fleet_query import create_fleet_query_crew

    return create_fleet_query_crew(tools, question)
