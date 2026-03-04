"""Async Ollama client wrapper for JennMesh AI features.

Optional dependency — all methods degrade gracefully when Ollama is unavailable.
Follows JennEdge's AsyncClient pattern (src/llm/ollama_client.py) but adapted
for fleet-management AI tasks: anomaly detection, alert summarization,
provisioning advice, and lost node reasoning.

Environment variables:
    OLLAMA_HOST  — Ollama server URL (default: http://localhost:11434)
    OLLAMA_MODEL — Model to use (default: qwen3:4b)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default Ollama configuration (reuses JennEdge's Ollama on same hardware)
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"


@dataclass
class AnomalyReport:
    """Result of Ollama anomaly analysis for a mesh node."""

    node_id: str
    is_anomalous: bool = False
    severity: str = "info"  # info, warning, critical
    summary: str = ""
    details: str = ""
    recommended_action: str = ""
    confidence: float = 0.0  # 0.0 - 1.0


@dataclass
class ProvisioningAdvice:
    """Ollama-generated deployment recommendations."""

    summary: str = ""
    recommended_roles: list[dict[str, str]] = field(default_factory=list)
    power_settings: str = ""
    channel_config: str = ""
    deployment_order: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class LocationReasoning:
    """Ollama-generated reasoning about a lost node's probable location."""

    node_id: str = ""
    probable_location: str = ""
    reasoning: str = ""
    search_recommendations: list[str] = field(default_factory=list)
    confidence: str = "low"  # low, medium, high


class OllamaClient:
    """Async wrapper for Ollama inference, shared by all JennMesh AI features.

    All public methods return structured results or None when Ollama is
    unavailable. No method raises on Ollama connection failure — they log
    warnings and return graceful fallbacks.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._host = host or os.environ.get("OLLAMA_HOST", DEFAULT_HOST)
        self._model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        self._client: Any = None  # Lazy-loaded ollama.AsyncClient
        self._available: Optional[bool] = None  # Cached availability

    @property
    def host(self) -> str:
        return self._host

    @property
    def model(self) -> str:
        return self._model

    def _get_client(self) -> Any:
        """Lazy-load the ollama AsyncClient (import-time safety)."""
        if self._client is None:
            try:
                from ollama import AsyncClient

                self._client = AsyncClient(host=self._host)
            except ImportError:
                logger.warning(
                    "ollama package not installed. " "Install with: pip install jenn-mesh[ollama]"
                )
                return None
        return self._client

    async def is_available(self) -> bool:
        """Check if Ollama server is reachable and model is loaded.

        Result is cached after first successful check.  Call
        ``reset_availability()`` to force a re-check.
        """
        if self._available is not None:
            return self._available

        client = self._get_client()
        if client is None:
            self._available = False
            return False

        try:
            # Ollama's list() returns available models
            response = await client.list()
            models = [m.get("name", "") if isinstance(m, dict) else str(m) for m in response.models]
            # Check if our model (or a variant) is available
            model_base = self._model.split(":")[0]
            self._available = any(model_base in str(m) for m in models)
            if not self._available:
                logger.warning(
                    "Ollama is running but model '%s' not found. "
                    "Available: %s. Run: ollama pull %s",
                    self._model,
                    [str(m) for m in models[:5]],
                    self._model,
                )
            return self._available
        except Exception as exc:
            logger.warning("Ollama server not reachable: %s", type(exc).__name__)
            self._available = False
            return False

    def reset_availability(self) -> None:
        """Clear cached availability so next call re-checks."""
        self._available = None

    async def chat(self, system_prompt: str, user_message: str) -> Optional[str]:
        """Send a chat completion request to Ollama.

        Returns the assistant's response text, or None if unavailable.
        """
        if not await self.is_available():
            return None

        client = self._get_client()
        if client is None:
            return None

        try:
            response = await client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            content = response.get("message", {}).get("content", "")
            # Strip <think> blocks from qwen3 reasoning models
            return _strip_think_tags(content)
        except Exception as exc:
            logger.error("Ollama chat failed: %s", type(exc).__name__)
            return None

    async def chat_json(self, system_prompt: str, user_message: str) -> Optional[dict[str, Any]]:
        """Chat with JSON output parsing. Returns parsed dict or None."""
        raw = await self.chat(system_prompt, user_message)
        if raw is None:
            return None
        try:
            return json.loads(_extract_json(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Ollama returned non-JSON response: %s", type(exc).__name__)
            return None

    # ── Feature-specific methods ─────────────────────────────────────

    async def analyze_anomaly(self, telemetry_context: dict) -> Optional[AnomalyReport]:
        """Analyze node telemetry for anomalies using LLM reasoning.

        Args:
            telemetry_context: Dict with keys: node_id, recent_samples,
                baseline, device_info, alert_history
        """
        node_id = telemetry_context.get("node_id", "unknown")
        system_prompt = (
            "You are a Meshtastic mesh network analyst. Analyze the telemetry data "
            "for anomalies (unusual signal patterns, battery drain, connectivity issues). "
            "Respond in JSON with keys: is_anomalous (bool), severity (info/warning/critical), "
            "summary (1-2 sentences), details (technical explanation), "
            "recommended_action (what operator should do), confidence (0.0-1.0)."
        )
        user_msg = json.dumps(telemetry_context, indent=2, default=str)
        result = await self.chat_json(system_prompt, user_msg)
        if result is None:
            return None
        return AnomalyReport(
            node_id=node_id,
            is_anomalous=result.get("is_anomalous", False),
            severity=result.get("severity", "info"),
            summary=result.get("summary", ""),
            details=result.get("details", ""),
            recommended_action=result.get("recommended_action", ""),
            confidence=float(result.get("confidence", 0.0)),
        )

    async def summarize_alerts(self, alerts: list[dict]) -> Optional[str]:
        """Collapse multiple alerts into a human-readable summary.

        Args:
            alerts: List of alert dicts with keys: node_id, alert_type,
                severity, message, created_at
        """
        if not alerts:
            return "No active alerts."
        system_prompt = (
            "You are a Meshtastic fleet health assistant. Summarize these mesh network "
            "alerts into a brief, actionable paragraph. Group related alerts together. "
            "Highlight the most critical issues first. Be concise — 3-5 sentences max."
        )
        user_msg = json.dumps(alerts, indent=2, default=str)
        return await self.chat(system_prompt, user_msg)

    async def advise_provisioning(self, deployment_context: dict) -> Optional[ProvisioningAdvice]:
        """Generate deployment recommendations for a new mesh deployment.

        Args:
            deployment_context: Dict with keys: terrain, num_nodes,
                power_source, desired_coverage_m, existing_nodes
        """
        system_prompt = (
            "You are a Meshtastic deployment expert. Based on the deployment context, "
            "recommend node roles, power settings, channel configuration, and deployment "
            "order. Respond in JSON with keys: summary (str), recommended_roles (list of "
            "{node_name: str, role: str, reason: str}), power_settings (str), "
            "channel_config (str), deployment_order (list of str), warnings (list of str)."
        )
        user_msg = json.dumps(deployment_context, indent=2, default=str)
        result = await self.chat_json(system_prompt, user_msg)
        if result is None:
            return None
        return ProvisioningAdvice(
            summary=result.get("summary", ""),
            recommended_roles=result.get("recommended_roles", []),
            power_settings=result.get("power_settings", ""),
            channel_config=result.get("channel_config", ""),
            deployment_order=result.get("deployment_order", []),
            warnings=result.get("warnings", []),
        )

    async def reason_lost_node(self, node_context: dict) -> Optional[LocationReasoning]:
        """Generate probabilistic location reasoning for a lost node.

        Args:
            node_context: Dict with keys: node_id, last_positions,
                battery_at_last_contact, movement_vector, environmental_conditions,
                nearby_nodes, time_since_last_contact
        """
        system_prompt = (
            "You are a search-and-rescue analyst for Meshtastic mesh radio nodes. "
            "Based on the node's last known data, estimate its probable location and "
            "provide search recommendations. Respond in JSON with keys: "
            "probable_location (description), reasoning (explanation), "
            "search_recommendations (list of str), confidence (low/medium/high)."
        )
        user_msg = json.dumps(node_context, indent=2, default=str)
        result = await self.chat_json(system_prompt, user_msg)
        if result is None:
            return None
        return LocationReasoning(
            node_id=node_context.get("node_id", ""),
            probable_location=result.get("probable_location", ""),
            reasoning=result.get("reasoning", ""),
            search_recommendations=result.get("search_recommendations", []),
            confidence=result.get("confidence", "low"),
        )

    async def health_info(self) -> dict[str, Any]:
        """Return health/status info for the health endpoint."""
        available = await self.is_available()
        return {
            "available": available,
            "host": self._host,
            "model": self._model,
        }


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from qwen3 reasoning output."""
    import re

    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _extract_json(text: str) -> str:
    """Extract the first JSON object or array from mixed text.

    Handles markdown code fences (```json ... ```) and bare JSON.
    """
    import re

    # Try markdown code fence first
    fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
    if fence_match:
        return fence_match.group(1).strip()

    # Try to find bare JSON object/array
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        if start >= 0:
            # Find the matching closing brace/bracket
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]
    return text.strip()
