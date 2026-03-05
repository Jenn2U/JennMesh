"""CrewAI tool wrappers for existing JennMesh AI services.

Each tool is a thin adapter — all real logic stays in the existing service classes.
Tools receive a reference to app.state services via a module-level registry
that is populated at init time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Module-level service registry, populated by init_crews()
_services: dict[str, Any] = {}


def set_services(services: dict[str, Any]) -> None:
    """Register app.state services for tool access."""
    _services.update(services)


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync context (CrewAI tools are sync)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result(timeout=60)
    return asyncio.run(coro)


def _get_service(name: str) -> Optional[Any]:
    """Get a registered service by name."""
    svc = _services.get(name)
    if svc is None:
        logger.warning("CrewAI tool requested unavailable service: %s", name)
    return svc


# ── Tool functions (used as CrewAI tools) ────────────────────────────


def analyze_node_tool(node_id: str) -> str:
    """Analyze a mesh node for telemetry anomalies.

    Uses baseline deviation detection + optional Ollama AI reasoning.
    Returns anomaly report as JSON string, or 'no anomaly' message.
    """
    detector = _get_service("anomaly_detector")
    if detector is None:
        return json.dumps({"error": "Anomaly detector not available"})
    result = _run_async(detector.analyze_node(node_id))
    if result is None:
        return json.dumps({"node_id": node_id, "is_anomalous": False})
    return json.dumps(result, default=str)


def analyze_fleet_tool() -> str:
    """Analyze all mesh nodes for anomalies across the entire fleet.

    Scans every registered device for baseline deviations.
    Returns list of anomaly reports as JSON string.
    """
    detector = _get_service("anomaly_detector")
    if detector is None:
        return json.dumps({"error": "Anomaly detector not available"})
    reports = _run_async(detector.analyze_fleet())
    return json.dumps({"anomaly_count": len(reports), "reports": reports}, default=str)


def summarize_alerts_tool() -> str:
    """Summarize all active fleet alerts into a human-readable report.

    Collapses alerts by type, severity, and affected nodes.
    Uses Ollama AI if available, otherwise rule-based summary.
    """
    summarizer = _get_service("alert_summarizer")
    if summarizer is None:
        return json.dumps({"error": "Alert summarizer not available"})
    result = _run_async(summarizer.summarize_active())
    return json.dumps(result, default=str)


def summarize_node_alerts_tool(node_id: str) -> str:
    """Summarize active alerts for a specific mesh node.

    Returns per-node alert summary with count and breakdown.
    """
    summarizer = _get_service("alert_summarizer")
    if summarizer is None:
        return json.dumps({"error": "Alert summarizer not available"})
    result = _run_async(summarizer.summarize_for_node(node_id))
    return json.dumps(result, default=str)


def query_fleet_tool(question: str) -> str:
    """Ask a natural language question about the mesh fleet.

    Supports questions about device status, alerts, topology,
    battery levels, firmware versions, and more.
    Returns conversational answer as JSON string.
    """
    engine = _get_service("fleet_query_engine")
    if engine is None:
        return json.dumps({"error": "Fleet query engine not available"})
    result = _run_async(engine.ask(question))
    return json.dumps({"answer": result.answer, "source": result.source}, default=str)


def reason_lost_node_tool(node_id: str) -> str:
    """Generate AI reasoning about a lost node's probable location.

    Analyzes position history, battery state, movement patterns,
    and nearby node topology to predict where the node might be.
    """
    reasoner = _get_service("lost_node_reasoner")
    if reasoner is None:
        return json.dumps({"error": "Lost node reasoner not available"})
    result = _run_async(reasoner.reason(node_id))
    return json.dumps(result, default=str)


def recommend_provisioning_tool(
    terrain: str = "urban",
    num_nodes: int = 3,
    power_source: str = "battery",
) -> str:
    """Generate deployment recommendations for new mesh nodes.

    Recommends node roles, power settings, channel config,
    and deployment order based on terrain and fleet context.
    """
    advisor = _get_service("provisioning_advisor")
    if advisor is None:
        return json.dumps({"error": "Provisioning advisor not available"})
    context = {
        "terrain": terrain,
        "num_nodes": num_nodes,
        "power_source": power_source,
    }
    result = _run_async(advisor.recommend(context))
    return json.dumps(result, default=str)


# Registry of all available tools for crew construction
ALL_TOOLS = {
    "analyze_node": analyze_node_tool,
    "analyze_fleet": analyze_fleet_tool,
    "summarize_alerts": summarize_alerts_tool,
    "summarize_node_alerts": summarize_node_alerts_tool,
    "query_fleet": query_fleet_tool,
    "reason_lost_node": reason_lost_node_tool,
    "recommend_provisioning": recommend_provisioning_tool,
}
