"""Tests for the ProvisioningAdvisor core module."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenn_mesh.core.provisioning_advisor import ProvisioningAdvisor
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db(tmp_path: Path) -> MeshDatabase:
    return MeshDatabase(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def advisor(db: MeshDatabase) -> ProvisioningAdvisor:
    return ProvisioningAdvisor(db=db)


# ── Deterministic Fallback ───────────────────────────────────────


class TestDeterministicAdvice:
    """Test the rule-based fallback when Ollama is unavailable."""

    @pytest.mark.asyncio
    async def test_basic_recommendation(self, advisor: ProvisioningAdvisor):
        """Basic deployment context returns valid advice."""
        result = await advisor.recommend(
            {"terrain": "urban", "num_nodes": 3, "power_source": "battery"}
        )
        assert result["source"] == "deterministic"
        assert len(result["recommended_roles"]) == 3
        assert result["power_settings"]
        assert result["channel_config"]

    @pytest.mark.asyncio
    async def test_single_node(self, advisor: ProvisioningAdvisor):
        """Single node gets ROUTER_CLIENT dual role."""
        result = await advisor.recommend({"num_nodes": 1})
        roles = result["recommended_roles"]
        assert len(roles) == 1
        assert roles[0]["role"] == "ROUTER_CLIENT"

    @pytest.mark.asyncio
    async def test_two_nodes(self, advisor: ProvisioningAdvisor):
        """Two nodes: one ROUTER, one CLIENT."""
        result = await advisor.recommend({"num_nodes": 2})
        role_set = {r["role"] for r in result["recommended_roles"]}
        assert "ROUTER" in role_set
        assert "CLIENT" in role_set

    @pytest.mark.asyncio
    async def test_large_fleet(self, advisor: ProvisioningAdvisor):
        """Large fleet: ~30% routers, rest clients."""
        result = await advisor.recommend({"num_nodes": 9})
        roles = result["recommended_roles"]
        assert len(roles) == 9
        router_count = sum(1 for r in roles if r["role"] == "ROUTER")
        assert router_count == 3  # 9 // 3

    @pytest.mark.asyncio
    async def test_power_source_solar(self, advisor: ProvisioningAdvisor):
        """Solar power source suggests medium TX power."""
        result = await advisor.recommend({"num_nodes": 2, "power_source": "solar"})
        assert "20 dBm" in result["power_settings"]

    @pytest.mark.asyncio
    async def test_power_source_mains(self, advisor: ProvisioningAdvisor):
        """Mains power suggests maximum TX power."""
        result = await advisor.recommend({"num_nodes": 2, "power_source": "mains"})
        assert "30 dBm" in result["power_settings"]

    @pytest.mark.asyncio
    async def test_terrain_mountainous(self, advisor: ProvisioningAdvisor):
        """Mountainous terrain recommends VeryLongSlow."""
        result = await advisor.recommend({"num_nodes": 3, "terrain": "mountainous"})
        assert "VeryLongSlow" in result["channel_config"]

    @pytest.mark.asyncio
    async def test_terrain_indoor(self, advisor: ProvisioningAdvisor):
        """Indoor terrain warns about reduced range."""
        result = await advisor.recommend({"num_nodes": 3, "terrain": "indoor"})
        assert any("Indoor" in w or "indoor" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_deployment_order(self, advisor: ProvisioningAdvisor):
        """Deployment order starts with routers."""
        result = await advisor.recommend({"num_nodes": 5})
        order = result["deployment_order"]
        # Routers should be deployed first
        assert order[0] == "Node-1"  # First router

    @pytest.mark.asyncio
    async def test_small_fleet_warning(self, advisor: ProvisioningAdvisor):
        """Fleet < 3 nodes warns about no redundancy."""
        result = await advisor.recommend({"num_nodes": 2})
        assert any("redundancy" in w.lower() for w in result["warnings"])


# ── Status ────────────────────────────────────────────────────────


class TestAdvisorStatus:
    def test_status_without_ollama(self, advisor: ProvisioningAdvisor):
        """Status shows ollama_available=False when no client."""
        status = advisor.get_status()
        assert status["enabled"] is True
        assert status["ollama_available"] is False
        assert status["source"] == "deterministic"
