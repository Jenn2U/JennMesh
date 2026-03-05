"""Fleet Health Analysis crew — multi-agent fleet health assessment."""

from __future__ import annotations

import logging
from typing import Any, Optional

from jenn_mesh.crews.config import CREWAI_LLM_MODEL, CREWAI_VERBOSE

logger = logging.getLogger(__name__)


def create_fleet_health_crew(tools: dict[str, Any]) -> Optional[Any]:
    """Create a Fleet Health Analysis crew.

    Agents:
        Fleet Analyst — scans fleet for anomalies and collects telemetry data
        Alert Interpreter — summarizes alerts and produces actionable report

    Returns CrewAI Crew instance, or None if crewai not installed.
    """
    try:
        from crewai import Agent, Crew, Process, Task
    except ImportError:
        logger.info("crewai not installed — fleet health crew unavailable")
        return None

    fleet_analyst = Agent(
        role="Fleet Health Analyst",
        goal=(
            "Scan the entire mesh fleet for anomalies, identify unhealthy nodes, "
            "and collect relevant telemetry data for deeper analysis."
        ),
        backstory=(
            "You are an expert Meshtastic mesh network analyst who monitors "
            "fleet health by checking for baseline deviations, offline nodes, "
            "and degraded signal quality across the entire network."
        ),
        tools=[tools["analyze_fleet"], tools["query_fleet"]],
        llm=CREWAI_LLM_MODEL,
        verbose=CREWAI_VERBOSE,
    )

    alert_interpreter = Agent(
        role="Alert Interpreter",
        goal=(
            "Analyze active alerts and anomaly reports to produce a concise, "
            "actionable fleet health summary with prioritized recommendations."
        ),
        backstory=(
            "You are a seasoned operations analyst who translates raw alerts "
            "and anomaly data into clear, prioritized action items for field "
            "technicians and fleet managers."
        ),
        tools=[tools["summarize_alerts"], tools["query_fleet"]],
        llm=CREWAI_LLM_MODEL,
        verbose=CREWAI_VERBOSE,
    )

    scan_task = Task(
        description=(
            "Analyze the entire fleet for anomalies. Identify any nodes with "
            "baseline deviations, signal degradation, or unusual battery drain. "
            "Query the fleet for offline devices and critical alerts."
        ),
        expected_output=(
            "A structured report listing: (1) anomalous nodes with deviation details, "
            "(2) offline or degraded nodes, (3) critical alerts by severity."
        ),
        agent=fleet_analyst,
    )

    interpret_task = Task(
        description=(
            "Using the fleet analyst's findings, summarize all active alerts "
            "and produce a prioritized action plan. Group issues by severity "
            "and affected nodes. Recommend specific remediation steps."
        ),
        expected_output=(
            "A concise fleet health report with: (1) executive summary (2-3 sentences), "
            "(2) critical issues requiring immediate attention, "
            "(3) warnings to monitor, (4) recommended next steps."
        ),
        agent=alert_interpreter,
    )

    return Crew(
        agents=[fleet_analyst, alert_interpreter],
        tasks=[scan_task, interpret_task],
        process=Process.sequential,
        verbose=CREWAI_VERBOSE,
    )
