"""Tests for OllamaClient — Ollama inference wrapper for JennMesh AI features.

All tests mock the ollama library since Ollama may not be installed in CI.
Tests verify:
    - Graceful degradation when Ollama unavailable
    - Chat and JSON parsing
    - Feature-specific methods (anomaly, summarization, provisioning, locator)
    - Think-tag stripping for qwen3 models
    - JSON extraction from mixed text
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jenn_mesh.inference.ollama_client import (
    AnomalyReport,
    LocationReasoning,
    OllamaClient,
    ProvisioningAdvice,
    _extract_json,
    _strip_think_tags,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_chat_response(content: str) -> dict:
    """Build a mock Ollama chat response."""
    return {"message": {"content": content}}


def _make_list_response(model_names: list[str]) -> SimpleNamespace:
    """Build a mock Ollama list() response with model names."""
    models = [{"name": name} for name in model_names]
    return SimpleNamespace(models=models)


# ── Core client tests ────────────────────────────────────────────────


class TestOllamaClientInit:
    """Initialization and configuration."""

    def test_default_host_and_model(self):
        client = OllamaClient()
        assert client.host == "http://localhost:11434"
        assert client.model == "qwen3:4b"

    def test_custom_host_and_model(self):
        client = OllamaClient(host="http://gpu-box:11434", model="llama3:8b")
        assert client.host == "http://gpu-box:11434"
        assert client.model == "llama3:8b"

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://from-env:11434")
        monkeypatch.setenv("OLLAMA_MODEL", "mistral:7b")
        client = OllamaClient()
        assert client.host == "http://from-env:11434"
        assert client.model == "mistral:7b"

    def test_explicit_params_override_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://from-env:11434")
        client = OllamaClient(host="http://explicit:11434")
        assert client.host == "http://explicit:11434"


class TestOllamaAvailability:
    """Availability checks and graceful degradation."""

    @pytest.mark.asyncio
    async def test_unavailable_when_import_fails(self):
        """When ollama package is not installed, client reports unavailable."""
        client = OllamaClient()
        with patch.object(client, "_get_client", return_value=None):
            assert await client.is_available() is False

    @pytest.mark.asyncio
    async def test_unavailable_when_server_unreachable(self):
        """When Ollama server is down, client reports unavailable."""
        mock_client = AsyncMock()
        mock_client.list = AsyncMock(side_effect=ConnectionError("refused"))
        client = OllamaClient()
        client._client = mock_client
        assert await client.is_available() is False

    @pytest.mark.asyncio
    async def test_available_when_model_present(self):
        """When Ollama is running and model is present, reports available."""
        mock_client = AsyncMock()
        mock_client.list = AsyncMock(return_value=_make_list_response(["qwen3:4b"]))
        client = OllamaClient()
        client._client = mock_client
        assert await client.is_available() is True

    @pytest.mark.asyncio
    async def test_unavailable_when_model_missing(self):
        """When Ollama is running but our model isn't loaded, reports unavailable."""
        mock_client = AsyncMock()
        mock_client.list = AsyncMock(return_value=_make_list_response(["llama3:8b"]))
        client = OllamaClient(model="qwen3:4b")
        client._client = mock_client
        assert await client.is_available() is False

    @pytest.mark.asyncio
    async def test_availability_cached(self):
        """Once checked, availability is cached until reset."""
        mock_client = AsyncMock()
        mock_client.list = AsyncMock(return_value=_make_list_response(["qwen3:4b"]))
        client = OllamaClient()
        client._client = mock_client

        assert await client.is_available() is True
        # Change the mock to return different models — cached result should persist
        mock_client.list = AsyncMock(return_value=_make_list_response([]))
        assert await client.is_available() is True  # still cached

        # Reset and re-check
        client.reset_availability()
        assert await client.is_available() is False

    @pytest.mark.asyncio
    async def test_health_info(self):
        """health_info() returns status dict for the health endpoint."""
        mock_client = AsyncMock()
        mock_client.list = AsyncMock(return_value=_make_list_response(["qwen3:4b"]))
        client = OllamaClient()
        client._client = mock_client
        info = await client.health_info()
        assert info["available"] is True
        assert info["host"] == "http://localhost:11434"
        assert info["model"] == "qwen3:4b"


class TestOllamaChat:
    """Chat and JSON parsing."""

    @pytest.mark.asyncio
    async def test_chat_returns_none_when_unavailable(self):
        client = OllamaClient()
        client._available = False
        result = await client.chat("system", "user")
        assert result is None

    @pytest.mark.asyncio
    async def test_chat_returns_content(self):
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=_make_chat_response("Hello from Ollama!"))
        client = OllamaClient()
        client._client = mock_client
        client._available = True

        result = await client.chat("You are helpful.", "Hi")
        assert result == "Hello from Ollama!"

    @pytest.mark.asyncio
    async def test_chat_strips_think_tags(self):
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(
            return_value=_make_chat_response(
                "<think>reasoning about this...</think>The actual answer."
            )
        )
        client = OllamaClient()
        client._client = mock_client
        client._available = True

        result = await client.chat("system", "user")
        assert result == "The actual answer."

    @pytest.mark.asyncio
    async def test_chat_json_parses_response(self):
        json_body = json.dumps({"is_anomalous": True, "severity": "warning"})
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=_make_chat_response(json_body))
        client = OllamaClient()
        client._client = mock_client
        client._available = True

        result = await client.chat_json("system", "user")
        assert result is not None
        assert result["is_anomalous"] is True
        assert result["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_chat_json_handles_markdown_fence(self):
        fenced = 'Here\'s the analysis:\n```json\n{"status": "ok"}\n```\nDone.'
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=_make_chat_response(fenced))
        client = OllamaClient()
        client._client = mock_client
        client._available = True

        result = await client.chat_json("system", "user")
        assert result is not None
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_chat_json_returns_none_on_invalid_json(self):
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=_make_chat_response("This is not JSON at all"))
        client = OllamaClient()
        client._client = mock_client
        client._available = True

        result = await client.chat_json("system", "user")
        assert result is None

    @pytest.mark.asyncio
    async def test_chat_handles_exception(self):
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=RuntimeError("timeout"))
        client = OllamaClient()
        client._client = mock_client
        client._available = True

        result = await client.chat("system", "user")
        assert result is None


# ── Feature-specific method tests ────────────────────────────────────


class TestAnalyzeAnomaly:
    @pytest.mark.asyncio
    async def test_returns_anomaly_report(self):
        analysis = {
            "is_anomalous": True,
            "severity": "warning",
            "summary": "Battery draining faster than baseline",
            "details": "30% drop in 2 hours vs 10% baseline",
            "recommended_action": "Check solar panel connection",
            "confidence": 0.85,
        }
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=_make_chat_response(json.dumps(analysis)))
        client = OllamaClient()
        client._client = mock_client
        client._available = True

        ctx = {"node_id": "!aaa11111", "recent_samples": [], "baseline": {}}
        report = await client.analyze_anomaly(ctx)
        assert report is not None
        assert isinstance(report, AnomalyReport)
        assert report.node_id == "!aaa11111"
        assert report.is_anomalous is True
        assert report.severity == "warning"
        assert report.confidence == 0.85

    @pytest.mark.asyncio
    async def test_returns_none_when_unavailable(self):
        client = OllamaClient()
        client._available = False
        report = await client.analyze_anomaly({"node_id": "!aaa11111"})
        assert report is None


class TestSummarizeAlerts:
    @pytest.mark.asyncio
    async def test_returns_summary_string(self):
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(
            return_value=_make_chat_response(
                "3 nodes have critical battery alerts. Node !ccc33333 is offline."
            )
        )
        client = OllamaClient()
        client._client = mock_client
        client._available = True

        alerts = [
            {"node_id": "!aaa", "alert_type": "low_battery", "severity": "warning"},
            {"node_id": "!ccc", "alert_type": "node_offline", "severity": "critical"},
        ]
        summary = await client.summarize_alerts(alerts)
        assert summary is not None
        assert "critical" in summary.lower() or "battery" in summary.lower()

    @pytest.mark.asyncio
    async def test_empty_alerts_returns_no_active(self):
        client = OllamaClient()
        client._available = True
        summary = await client.summarize_alerts([])
        assert summary == "No active alerts."


class TestAdviseProvisioning:
    @pytest.mark.asyncio
    async def test_returns_provisioning_advice(self):
        advice_json = {
            "summary": "Deploy 5 nodes in star topology",
            "recommended_roles": [{"node_name": "hub", "role": "ROUTER"}],
            "power_settings": "TX power 20dBm for outdoor",
            "channel_config": "Use LongFast modem preset",
            "deployment_order": ["hub", "spoke-1", "spoke-2"],
            "warnings": ["Avoid placing near metal structures"],
        }
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=_make_chat_response(json.dumps(advice_json)))
        client = OllamaClient()
        client._client = mock_client
        client._available = True

        ctx = {"terrain": "urban", "num_nodes": 5}
        advice = await client.advise_provisioning(ctx)
        assert advice is not None
        assert isinstance(advice, ProvisioningAdvice)
        assert "star" in advice.summary
        assert len(advice.deployment_order) == 3
        assert len(advice.warnings) == 1


class TestReasonLostNode:
    @pytest.mark.asyncio
    async def test_returns_location_reasoning(self):
        reasoning_json = {
            "probable_location": "Near the warehouse loading dock",
            "reasoning": "Last GPS was moving NW at 3km/h. Battery was at 8%.",
            "search_recommendations": ["Check loading dock area", "Scan with BLE"],
            "confidence": "medium",
        }
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=_make_chat_response(json.dumps(reasoning_json)))
        client = OllamaClient()
        client._client = mock_client
        client._available = True

        ctx = {"node_id": "!ccc33333", "last_positions": []}
        result = await client.reason_lost_node(ctx)
        assert result is not None
        assert isinstance(result, LocationReasoning)
        assert result.node_id == "!ccc33333"
        assert result.confidence == "medium"
        assert len(result.search_recommendations) == 2


# ── Utility function tests ───────────────────────────────────────────


class TestStripThinkTags:
    def test_removes_think_block(self):
        text = "<think>long reasoning...</think>Final answer."
        assert _strip_think_tags(text) == "Final answer."

    def test_removes_multiline_think(self):
        text = "<think>\nStep 1\nStep 2\n</think>\nResult here."
        assert _strip_think_tags(text) == "Result here."

    def test_passthrough_without_tags(self):
        text = "No think tags here."
        assert _strip_think_tags(text) == "No think tags here."

    def test_removes_multiple_think_blocks(self):
        text = "<think>a</think>First.<think>b</think>Second."
        assert _strip_think_tags(text) == "First.Second."


class TestExtractJson:
    def test_extracts_bare_json_object(self):
        text = 'Some text {"key": "value"} more text'
        result = json.loads(_extract_json(text))
        assert result == {"key": "value"}

    def test_extracts_from_markdown_fence(self):
        text = '```json\n{"status": "ok"}\n```'
        result = json.loads(_extract_json(text))
        assert result == {"status": "ok"}

    def test_extracts_json_array(self):
        text = "Result: [1, 2, 3]"
        result = json.loads(_extract_json(text))
        assert result == [1, 2, 3]

    def test_handles_nested_json(self):
        text = '{"outer": {"inner": [1, 2]}}'
        result = json.loads(_extract_json(text))
        assert result["outer"]["inner"] == [1, 2]

    def test_handles_plain_text(self):
        text = "no json here"
        assert _extract_json(text) == "no json here"
