"""Fleet Query crew — multi-agent natural language fleet queries."""

from __future__ import annotations

import logging
from typing import Any, Optional

from jenn_mesh.crews.config import CREWAI_LLM_MODEL, CREWAI_VERBOSE

logger = logging.getLogger(__name__)


def create_fleet_query_crew(tools: dict[str, Any], question: str) -> Optional[Any]:
    """Create a Fleet Query crew for a natural language question.

    Agents:
        Query Interpreter — parses the question and gathers data
        Data Analyst — analyzes results and provides conversational answer

    Args:
        tools: Dict of CrewAI tool functions.
        question: Natural language question about the fleet.

    Returns CrewAI Crew instance, or None if crewai not installed.
    """
    try:
        from crewai import Agent, Crew, Process, Task
    except ImportError:
        logger.info("crewai not installed — fleet query crew unavailable")
        return None

    interpreter = Agent(
        role="Query Interpreter",
        goal=(
            "Understand the user's fleet question and gather all relevant "
            "data by querying the fleet, checking alerts, and analyzing "
            "anomalies as needed."
        ),
        backstory=(
            "You are a fleet data specialist who translates natural language "
            "questions into structured queries. You know which tools to use "
            "for different types of fleet questions."
        ),
        tools=[
            tools["query_fleet"],
            tools["analyze_fleet"],
            tools["summarize_alerts"],
        ],
        llm=CREWAI_LLM_MODEL,
        verbose=CREWAI_VERBOSE,
    )

    analyst = Agent(
        role="Data Analyst",
        goal=(
            "Analyze the gathered fleet data and provide a clear, concise, "
            "and actionable answer to the user's question."
        ),
        backstory=(
            "You are a data analyst who excels at interpreting mesh network "
            "data and presenting findings in clear, non-technical language."
        ),
        tools=[tools["query_fleet"]],
        llm=CREWAI_LLM_MODEL,
        verbose=CREWAI_VERBOSE,
    )

    gather_task = Task(
        description=(
            f'The user asked: "{question}"\n\n'
            "Gather all relevant data to answer this question. Use the fleet "
            "query tool for device/topology questions, the anomaly tool for "
            "health concerns, and the alert summarizer for alert-related queries."
        ),
        expected_output=(
            "Raw data relevant to the user's question, organized by source "
            "(fleet query results, anomaly reports, alert summaries)."
        ),
        agent=interpreter,
    )

    analyze_task = Task(
        description=(
            f'Based on the gathered data, answer the user\'s question: "{question}"\n\n'
            "Provide a concise, conversational answer. Use specific numbers "
            "and device names. Keep it under 150 words."
        ),
        expected_output=(
            "A clear, conversational answer to the user's question with "
            "specific data points and any relevant recommendations."
        ),
        agent=analyst,
    )

    return Crew(
        agents=[interpreter, analyst],
        tasks=[gather_task, analyze_task],
        process=Process.sequential,
        verbose=CREWAI_VERBOSE,
    )
