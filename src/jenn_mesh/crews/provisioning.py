"""Provisioning Advisory crew — multi-agent deployment planning."""

from __future__ import annotations

import logging
from typing import Any, Optional

from jenn_mesh.crews.config import CREWAI_LLM_MODEL, CREWAI_VERBOSE

logger = logging.getLogger(__name__)


def create_provisioning_crew(tools: dict[str, Any], context: dict[str, Any]) -> Optional[Any]:
    """Create a Provisioning Advisory crew.

    Agents:
        Fleet Surveyor — analyzes current fleet state and coverage gaps
        Deployment Planner — generates provisioning recommendations

    Args:
        tools: Dict of CrewAI tool functions.
        context: Deployment context (terrain, num_nodes, power_source, etc.).

    Returns CrewAI Crew instance, or None if crewai not installed.
    """
    try:
        from crewai import Agent, Crew, Process, Task
    except ImportError:
        logger.info("crewai not installed — provisioning crew unavailable")
        return None

    terrain = context.get("terrain", "urban")
    num_nodes = context.get("num_nodes", 3)
    power_source = context.get("power_source", "battery")

    surveyor = Agent(
        role="Fleet Surveyor",
        goal=(
            "Assess the current mesh fleet state — device count, roles, "
            "topology health, coverage gaps, and any existing issues that "
            "should inform new node deployment."
        ),
        backstory=(
            "You are a network survey specialist who maps existing mesh "
            "infrastructure before recommending expansions. You identify "
            "coverage gaps, single points of failure, and capacity limits."
        ),
        tools=[tools["query_fleet"]],
        llm=CREWAI_LLM_MODEL,
        verbose=CREWAI_VERBOSE,
    )

    planner = Agent(
        role="Deployment Planner",
        goal=(
            f"Design an optimal deployment plan for {num_nodes} new nodes "
            f"in {terrain} terrain using {power_source} power, considering "
            "the fleet survey findings."
        ),
        backstory=(
            "You are a mesh network deployment architect who designs "
            "optimal node placements, role assignments, and channel "
            "configurations for field deployments."
        ),
        tools=[tools["recommend_provisioning"], tools["query_fleet"]],
        llm=CREWAI_LLM_MODEL,
        verbose=CREWAI_VERBOSE,
    )

    survey_task = Task(
        description=(
            "Survey the current fleet: "
            "(1) How many nodes are online/offline? "
            "(2) What roles are assigned (routers vs clients)? "
            "(3) Are there single points of failure? "
            "(4) What is the current alert situation?"
        ),
        expected_output=(
            "Fleet survey report: node count, role distribution, "
            "topology health, coverage assessment, active issues."
        ),
        agent=surveyor,
    )

    plan_task = Task(
        description=(
            f"Using the fleet survey, generate deployment recommendations for "
            f"{num_nodes} new {power_source}-powered nodes in {terrain} terrain. "
            "Include role assignments, power settings, channel config, "
            "deployment order, and integration warnings."
        ),
        expected_output=(
            "Deployment plan with: (1) recommended roles per node, "
            "(2) power and channel settings, (3) deployment sequence, "
            "(4) integration notes with existing fleet, (5) risk warnings."
        ),
        agent=planner,
    )

    return Crew(
        agents=[surveyor, planner],
        tasks=[survey_task, plan_task],
        process=Process.sequential,
        verbose=CREWAI_VERBOSE,
    )
