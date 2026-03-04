"""Tests for the LostNodeReasoner core module."""

from __future__ import annotations

import pytest

from jenn_mesh.core.lost_node_reasoner import LostNodeReasoner
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def reasoner(populated_db: MeshDatabase) -> LostNodeReasoner:
    return LostNodeReasoner(db=populated_db)


# ── Deterministic Reasoning ──────────────────────────────────────


class TestDeterministicReasoning:
    """Test rule-based reasoning when Ollama is unavailable."""

    @pytest.mark.asyncio
    async def test_reason_node_with_position(self, reasoner: LostNodeReasoner):
        """Node with GPS history returns position-based reasoning."""
        result = await reasoner.reason("!ccc33333")
        assert result["node_id"] == "!ccc33333"
        assert result["source"] == "deterministic"
        assert result["confidence"] in ("low", "medium", "high")
        assert result["probable_location"]
        assert result["reasoning"]

    @pytest.mark.asyncio
    async def test_reason_node_with_low_battery(self, reasoner: LostNodeReasoner):
        """Node with low battery includes battery reasoning."""
        result = await reasoner.reason("!ccc33333")
        # !ccc33333 has 15% battery
        assert "battery" in result["reasoning"].lower() or "power" in result["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_reason_unknown_node(self, reasoner: LostNodeReasoner):
        """Unknown node returns low confidence."""
        result = await reasoner.reason("!unknown999")
        assert result["confidence"] == "low"
        assert (
            "Unknown" in result["probable_location"]
            or "unknown" in result["probable_location"].lower()
        )

    @pytest.mark.asyncio
    async def test_reason_includes_context(self, reasoner: LostNodeReasoner):
        """Reasoning includes the context dict for transparency."""
        result = await reasoner.reason("!aaa11111")
        assert "context" in result
        assert result["context"]["node_id"] == "!aaa11111"

    @pytest.mark.asyncio
    async def test_reason_stationary_node(self, reasoner: LostNodeReasoner):
        """Router node reasoning mentions stationary nature."""
        result = await reasoner.reason("!aaa11111")
        # !aaa11111 is a ROUTER
        assert "stationary" in result["reasoning"].lower() or "relay" in result["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_search_recommendations_present(self, reasoner: LostNodeReasoner):
        """Result includes search recommendations."""
        result = await reasoner.reason("!ccc33333")
        assert isinstance(result["search_recommendations"], list)
        assert len(result["search_recommendations"]) > 0

    @pytest.mark.asyncio
    async def test_reason_node_no_position(self, reasoner: LostNodeReasoner):
        """Node with no GPS data has appropriate message."""
        result = await reasoner.reason("!ddd44444")
        # !ddd44444 is a sensor with no position data
        assert (
            "no" in result["probable_location"].lower()
            or "unknown" in result["probable_location"].lower()
        )


# ── Context Building ─────────────────────────────────────────────


class TestContextBuilding:
    def test_build_context_known_node(self, reasoner: LostNodeReasoner):
        """Context includes device info for known node."""
        ctx = reasoner._build_context("!aaa11111")
        assert ctx["device"]["node_id"] == "!aaa11111"
        assert ctx["device"]["role"] == "ROUTER"

    def test_build_context_positions(self, reasoner: LostNodeReasoner):
        """Context includes position history."""
        ctx = reasoner._build_context("!aaa11111")
        assert isinstance(ctx["last_positions"], list)
        # !aaa11111 has at least 1 position from conftest
        assert len(ctx["last_positions"]) >= 1

    def test_build_context_unknown_node(self, reasoner: LostNodeReasoner):
        """Context for unknown node has empty device info."""
        ctx = reasoner._build_context("!unknown999")
        assert ctx["device"] == {}
        assert ctx["last_positions"] == []


# ── Compass Direction ─────────────────────────────────────────────


class TestCompassDirection:
    def test_north(self):
        assert LostNodeReasoner._compass_direction(1.0, 0.0) == "north"

    def test_south(self):
        assert LostNodeReasoner._compass_direction(-1.0, 0.0) == "south"

    def test_east(self):
        assert LostNodeReasoner._compass_direction(0.0, 1.0) == "east"

    def test_west(self):
        assert LostNodeReasoner._compass_direction(0.0, -1.0) == "west"


# ── Status ────────────────────────────────────────────────────────


class TestReasonerStatus:
    def test_status_without_ollama(self, reasoner: LostNodeReasoner):
        status = reasoner.get_status()
        assert status["enabled"] is True
        assert status["ollama_available"] is False
