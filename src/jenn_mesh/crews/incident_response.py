"""Incident Response crew — multi-agent incident investigation and recovery."""

from __future__ import annotations

import logging
from typing import Any, Optional

from jenn_mesh.crews.config import CREWAI_LLM_MODEL, CREWAI_VERBOSE

logger = logging.getLogger(__name__)


def create_incident_response_crew(tools: dict[str, Any], node_id: str) -> Optional[Any]:
    """Create an Incident Response crew for a specific node.

    Agents:
        Incident Investigator — analyzes node anomalies and location data
        Recovery Planner — produces recovery recommendations

    Args:
        tools: Dict of CrewAI tool functions.
        node_id: The affected mesh node ID (e.g. "!aaa11111").

    Returns CrewAI Crew instance, or None if crewai not installed.
    """
    try:
        from crewai import Agent, Crew, Process, Task
    except ImportError:
        logger.info("crewai not installed — incident response crew unavailable")
        return None

    investigator = Agent(
        role="Incident Investigator",
        goal=(
            f"Thoroughly investigate the incident on node {node_id}. "
            "Determine root cause by analyzing anomalies, alert history, "
            "and node location data."
        ),
        backstory=(
            "You are a field operations investigator specializing in mesh "
            "network failures. You methodically gather evidence from telemetry, "
            "alerts, and location data to determine what happened to a node."
        ),
        tools=[
            tools["analyze_node"],
            tools["summarize_node_alerts"],
            tools["reason_lost_node"],
        ],
        llm=CREWAI_LLM_MODEL,
        verbose=CREWAI_VERBOSE,
    )

    recovery_planner = Agent(
        role="Recovery Planner",
        goal=(
            "Based on the investigation findings, create a concrete recovery "
            "plan with specific steps to restore the affected node or mitigate "
            "the impact on the mesh network."
        ),
        backstory=(
            "You are an operations recovery specialist who designs practical "
            "recovery procedures for mesh network incidents. You prioritize "
            "actions by urgency and feasibility."
        ),
        tools=[tools["query_fleet"]],
        llm=CREWAI_LLM_MODEL,
        verbose=CREWAI_VERBOSE,
    )

    investigate_task = Task(
        description=(
            f"Investigate node {node_id}: "
            "(1) Check for anomalies in its telemetry, "
            "(2) Review its active alerts, "
            "(3) If the node appears lost, analyze its probable location. "
            "Compile all findings into a structured incident report."
        ),
        expected_output=(
            f"Incident report for {node_id} containing: "
            "(1) anomaly analysis results, (2) alert summary, "
            "(3) location reasoning if applicable, (4) probable root cause."
        ),
        agent=investigator,
    )

    recovery_task = Task(
        description=(
            "Using the investigation report, create a prioritized recovery plan. "
            "Include: immediate actions, short-term mitigations, and any fleet-wide "
            "implications. Check fleet status for related issues on nearby nodes."
        ),
        expected_output=(
            "Recovery plan with: (1) immediate actions (next 1 hour), "
            "(2) short-term mitigations (next 24 hours), "
            "(3) fleet-wide impact assessment, (4) prevention recommendations."
        ),
        agent=recovery_planner,
    )

    return Crew(
        agents=[investigator, recovery_planner],
        tasks=[investigate_task, recovery_task],
        process=Process.sequential,
        verbose=CREWAI_VERBOSE,
    )
