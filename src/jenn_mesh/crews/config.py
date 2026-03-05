"""CrewAI configuration — no-op safe, opt-in via CREWAI_ENABLED env var."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

CREWAI_ENABLED = os.getenv("CREWAI_ENABLED", "").lower() in ("1", "true", "yes")
CREWAI_LLM_MODEL = os.getenv("CREWAI_LLM_MODEL", "ollama/qwen3:4b")
CREWAI_VERBOSE = os.getenv("CREWAI_VERBOSE", "false").lower() == "true"
