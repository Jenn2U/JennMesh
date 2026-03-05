"""Async Ollama client wrapper for JennMesh AI features.

Optional dependency — all methods degrade gracefully when Ollama is unavailable.
Follows JennEdge's AsyncClient pattern (src/llm/ollama_client.py) but adapted
for fleet-management AI tasks: anomaly detection, alert summarization,
provisioning advice, and lost node reasoning.

Supports:
    - Function calling via Ollama's native tools API
    - Pydantic-validated structured output via Instructor (auto-retry on schema violations)

Environment variables:
    OLLAMA_HOST  — Ollama server URL (default: http://localhost:11434)
    OLLAMA_MODEL — Model to use (default: qwen3:4b)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Default Ollama configuration (reuses JennEdge's Ollama on same hardware)
DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"
DEFAULT_CODE_MODEL = "qwen2.5-coder:7b"

# Vision-capable models that accept images alongside text prompts
VISION_MODELS: frozenset = frozenset(
    {
        "bakllava",
        "llava",
        "llava-llama3",
        "llava-phi3",
        "moondream",
        "moondream2",
        "minicpm-v",
        "qwen2-vl",
    }
)


def _is_vision_model(model: str) -> bool:
    """Check if a model supports vision/image input."""
    base = model.split(":")[0].lower()
    return base in VISION_MODELS or any(base.startswith(v) for v in VISION_MODELS)

T = TypeVar("T", bound=BaseModel)


class AnomalyReport(BaseModel):
    """Result of Ollama anomaly analysis for a mesh node."""

    node_id: str = ""
    is_anomalous: bool = False
    severity: str = Field(default="info", description="info, warning, or critical")
    summary: str = Field(default="", description="1-2 sentence summary")
    details: str = Field(default="", description="Technical explanation")
    recommended_action: str = Field(default="", description="What operator should do")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ProvisioningAdvice(BaseModel):
    """Ollama-generated deployment recommendations."""

    summary: str = ""
    recommended_roles: list[dict[str, str]] = Field(default_factory=list)
    power_settings: str = ""
    channel_config: str = ""
    deployment_order: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LocationReasoning(BaseModel):
    """Ollama-generated reasoning about a lost node's probable location."""

    node_id: str = ""
    probable_location: str = Field(default="", description="Estimated location description")
    reasoning: str = Field(default="", description="Explanation of reasoning")
    search_recommendations: list[str] = Field(default_factory=list)
    confidence: str = Field(default="low", description="low, medium, or high")


class OllamaClient:
    """Async wrapper for Ollama inference, shared by all JennMesh AI features.

    All public methods return structured results or None when Ollama is
    unavailable. No method raises on Ollama connection failure — they log
    warnings and return graceful fallbacks.

    Supports:
    - Function calling via ``tools`` parameter (Ollama native)
    - Pydantic-validated structured output via ``chat_structured()`` (Instructor)
    """

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        code_model: Optional[str] = None,
        proxy_url: Optional[str] = None,
        proxy_api_key: Optional[str] = None,
    ):
        self._host = host or os.environ.get("OLLAMA_HOST", DEFAULT_HOST)
        self._model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        self._code_model = code_model or os.environ.get("OLLAMA_CODE_MODEL", DEFAULT_CODE_MODEL)
        self._proxy_url = proxy_url or os.environ.get("LITELLM_PROXY_URL")
        self._proxy_api_key = proxy_api_key or os.environ.get(
            "LITELLM_API_KEY", "sk-jenn-litellm-local"
        )
        self._client: Any = None  # Lazy-loaded ollama.AsyncClient
        self._instructor_client: Any = None  # Lazy-loaded instructor client
        self._proxy_http: Any = None  # Lazy-loaded httpx client for proxy
        self._available: Optional[bool] = None  # Cached availability
        self._code_model_available: Optional[bool] = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def model(self) -> str:
        return self._model

    @property
    def code_model(self) -> str:
        return self._code_model

    @property
    def proxy_url(self) -> Optional[str]:
        return self._proxy_url

    def _get_proxy_http(self) -> Any:
        """Lazy-load httpx.AsyncClient for LiteLLM proxy requests."""
        if self._proxy_http is None:
            try:
                import httpx

                self._proxy_http = httpx.AsyncClient(timeout=120.0)
            except ImportError:
                logger.warning("httpx not installed — proxy routing unavailable")
                return None
        return self._proxy_http

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

    def _get_instructor_client(self) -> Any:
        """Lazy-load the Instructor client for Pydantic-validated output."""
        if self._instructor_client is None:
            try:
                import instructor

                self._instructor_client = instructor.from_provider(
                    f"ollama/{self._model}",
                    base_url=f"{self._host}/v1",
                    mode=instructor.Mode.JSON,
                )
            except ImportError:
                logger.warning(
                    "instructor package not installed. "
                    "Install with: pip install jenn-mesh[ollama]"
                )
                return None
        return self._instructor_client

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

    @property
    def capabilities(self) -> Dict[str, Any]:
        """Report client capabilities (vision depends on configured model)."""
        return {
            "vision": _is_vision_model(self._model),
            "function_calling": True,
            "json_output": True,
        }

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        images: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Send a chat completion request to Ollama.

        Args:
            system_prompt: System instruction.
            user_message: User message.
            tools: Optional Ollama function-calling tool schemas.
            images: Optional list of base64-encoded images for vision models.

        Returns the assistant's response text, raw response for tool calls,
        or None if unavailable.
        """
        # Try LiteLLM proxy first (fleet load balancing) — non-tool requests only
        if self._proxy_url and not tools:
            proxy_result = await self._try_proxy_chat(system_prompt, user_message, images)
            if proxy_result is not None:
                return proxy_result
            # Proxy failed — fall through to direct Ollama

        if not await self.is_available():
            return None

        client = self._get_client()
        if client is None:
            return None

        try:
            kwargs: Dict[str, Any] = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            }
            if tools:
                kwargs["tools"] = tools
            if images:
                kwargs["images"] = images

            response = await client.chat(**kwargs)

            # Return raw response for tool call dispatch
            if (
                tools
                and hasattr(response, "message")
                and hasattr(response.message, "tool_calls")
                and response.message.tool_calls
            ):
                return response

            content = response.get("message", {}).get("content", "")
            # Strip <think> blocks from qwen3 reasoning models
            return _strip_think_tags(content)
        except Exception as exc:
            logger.error("Ollama chat failed: %s", type(exc).__name__)
            return None

    async def _try_proxy_chat(
        self,
        system_prompt: str,
        user_message: str,
        images: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Attempt a chat completion via the LiteLLM proxy (OpenAI format).

        Returns the response text, or None on any failure (so caller can
        fall through to direct Ollama).
        """
        http = self._get_proxy_http()
        if http is None:
            return None

        proxy_url = self._proxy_url.rstrip("/")  # type: ignore[union-attr]
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        # Convert images to OpenAI multipart content format
        if images:
            content_parts: List[Dict[str, Any]] = [
                {"type": "text", "text": user_message}
            ]
            for img in images:
                content_parts.append(
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}
                )
            messages[-1]["content"] = content_parts

        payload = {
            "model": "default",
            "messages": messages,
            "temperature": 0.7,
        }

        try:
            response = await http.post(
                f"{proxy_url}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._proxy_api_key}"},
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return _strip_think_tags(content)
        except Exception as exc:
            logger.warning("LiteLLM proxy chat failed, falling back to direct Ollama: %s", exc)
            return None

    async def chat_json(self, system_prompt: str, user_message: str) -> Optional[dict[str, Any]]:
        """Chat with JSON output parsing. Returns parsed dict or None.

        Note: For new code, prefer ``chat_structured()`` which uses Instructor
        for Pydantic-validated output with auto-retry.
        """
        raw = await self.chat(system_prompt, user_message)
        if raw is None:
            return None
        try:
            return json.loads(_extract_json(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Ollama returned non-JSON response: %s", type(exc).__name__)
            return None

    async def chat_structured(
        self,
        response_model: Type[T],
        system_prompt: str,
        user_message: str,
        max_retries: int = 2,
    ) -> Optional[T]:
        """Chat with Pydantic-validated structured output via Instructor.

        Uses Instructor to automatically validate the LLM response against
        the given Pydantic model schema. If the response doesn't match,
        Instructor retries with validation error feedback.

        Args:
            response_model: Pydantic model class to validate against.
            system_prompt: System instruction.
            user_message: User message.
            max_retries: Number of retries on validation failure (default: 2).

        Returns:
            Validated Pydantic model instance, or None if unavailable/failed.
        """
        if not await self.is_available():
            return None

        instructor_client = self._get_instructor_client()
        if instructor_client is None:
            # Fall back to chat_json + manual construction
            logger.debug("Instructor unavailable, falling back to chat_json")
            raw = await self.chat_json(system_prompt, user_message)
            if raw is None:
                return None
            try:
                return response_model.model_validate(raw)
            except Exception as exc:
                logger.warning("Fallback validation failed: %s", type(exc).__name__)
                return None

        try:
            result = instructor_client.create(
                model=f"ollama/{self._model}",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                response_model=response_model,
                max_retries=max_retries,
            )
            return result
        except Exception as exc:
            logger.error("Instructor structured output failed: %s", type(exc).__name__)
            return None

    # ── Feature-specific methods ─────────────────────────────────────

    async def analyze_anomaly(self, telemetry_context: dict) -> Optional[AnomalyReport]:
        """Analyze node telemetry for anomalies using LLM reasoning.

        Uses Instructor for Pydantic-validated output with auto-retry.

        Args:
            telemetry_context: Dict with keys: node_id, recent_samples,
                baseline, device_info, alert_history
        """
        node_id = telemetry_context.get("node_id", "unknown")
        system_prompt = (
            "You are a Meshtastic mesh network analyst. Analyze the telemetry data "
            "for anomalies (unusual signal patterns, battery drain, connectivity issues)."
        )
        user_msg = json.dumps(telemetry_context, indent=2, default=str)
        report = await self.chat_structured(AnomalyReport, system_prompt, user_msg)
        if report is not None:
            report.node_id = node_id
        return report

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

        Uses Instructor for Pydantic-validated output with auto-retry.

        Args:
            deployment_context: Dict with keys: terrain, num_nodes,
                power_source, desired_coverage_m, existing_nodes
        """
        system_prompt = (
            "You are a Meshtastic deployment expert. Based on the deployment context, "
            "recommend node roles, power settings, channel configuration, and deployment "
            "order."
        )
        user_msg = json.dumps(deployment_context, indent=2, default=str)
        return await self.chat_structured(ProvisioningAdvice, system_prompt, user_msg)

    async def reason_lost_node(self, node_context: dict) -> Optional[LocationReasoning]:
        """Generate probabilistic location reasoning for a lost node.

        Uses Instructor for Pydantic-validated output with auto-retry.

        Args:
            node_context: Dict with keys: node_id, last_positions,
                battery_at_last_contact, movement_vector, environmental_conditions,
                nearby_nodes, time_since_last_contact
        """
        system_prompt = (
            "You are a search-and-rescue analyst for Meshtastic mesh radio nodes. "
            "Based on the node's last known data, estimate its probable location and "
            "provide search recommendations."
        )
        user_msg = json.dumps(node_context, indent=2, default=str)
        result = await self.chat_structured(LocationReasoning, system_prompt, user_msg)
        if result is not None:
            result.node_id = node_context.get("node_id", "")
        return result

    async def _is_code_model_available(self) -> bool:
        """Check if the code model is available on Ollama."""
        if self._code_model_available is not None:
            return self._code_model_available

        client = self._get_client()
        if client is None:
            self._code_model_available = False
            return False

        try:
            response = await client.list()
            models = [m.get("name", "") if isinstance(m, dict) else str(m) for m in response.models]
            model_base = self._code_model.split(":")[0]
            self._code_model_available = any(model_base in str(m) for m in models)
            return self._code_model_available
        except Exception:
            self._code_model_available = False
            return False

    async def _chat_code(self, system_prompt: str, user_message: str) -> Optional[str]:
        """Chat using the code model. Falls back to None if unavailable."""
        if not await self._is_code_model_available():
            return None

        client = self._get_client()
        if client is None:
            return None

        try:
            response = await client.chat(
                model=self._code_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            content = response.get("message", {}).get("content", "")
            return content.strip()
        except Exception as exc:
            logger.error("Code model chat failed: %s", type(exc).__name__)
            return None

    async def generate_config_yaml(self, deployment_context: dict) -> Optional[str]:
        """Generate a Meshtastic YAML configuration using the code model.

        Uses qwen2.5-coder for structured YAML output (better at code/config
        generation than the general model). Falls back to None when the code
        model is unavailable.

        Args:
            deployment_context: Dict with keys: node_role, region,
                channel_name, hop_limit, power_level, position_enabled
        """
        system_prompt = (
            "You are a Meshtastic configuration expert. Generate a valid "
            "Meshtastic YAML configuration file based on the deployment context. "
            "Output ONLY valid YAML with no markdown fences or explanations. "
            "Include sections: lora, channels, device, position, power as needed."
        )
        user_msg = json.dumps(deployment_context, indent=2, default=str)
        return await self._chat_code(system_prompt, user_msg)

    async def analyze_recovery_script(self, script_content: str) -> Optional[dict[str, Any]]:
        """Analyze a recovery script for safety before execution.

        Uses the code model to check for dangerous commands, permission issues,
        and side effects. Falls back to None when the code model is unavailable.

        Args:
            script_content: Shell script to analyze

        Returns:
            Dict with keys: safe (bool), risks (list[str]), suggestions (list[str])
        """
        system_prompt = (
            "You are a Linux system administration safety analyst. Analyze this "
            "shell script for safety concerns. Check for: dangerous commands "
            "(rm -rf, dd, mkfs), permission escalation, network access, data "
            "destruction, unintended side effects. Respond in JSON with keys: "
            "safe (bool), risks (list of strings), suggestions (list of strings)."
        )
        raw = await self._chat_code(system_prompt, script_content)
        if raw is None:
            return None
        try:
            return json.loads(_extract_json(raw))
        except (json.JSONDecodeError, ValueError):
            logger.warning("Code model returned non-JSON for script analysis")
            return None

    async def health_info(self) -> dict[str, Any]:
        """Return health/status info for the health endpoint."""
        available = await self.is_available()
        code_available = await self._is_code_model_available()
        return {
            "available": available,
            "host": self._host,
            "model": self._model,
            "code_model": self._code_model,
            "code_model_available": code_available,
            "proxy_url": self._proxy_url,
            "proxy_enabled": self._proxy_url is not None,
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
