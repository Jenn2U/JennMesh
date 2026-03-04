"""Provisioning advisor — Ollama-powered deployment recommendations.

Analyzes existing fleet topology and deployment context to recommend
optimal node roles, power settings, and channel configuration for
new or expanding mesh deployments.

When Ollama is unavailable, falls back to deterministic heuristics.
"""

from __future__ import annotations

import logging

from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)

# Deterministic role assignment heuristics
_DEFAULT_ROLES = {
    1: [{"node_name": "Node-1", "role": "ROUTER_CLIENT", "reason": "Single node — dual role"}],
    2: [
        {"node_name": "Node-1", "role": "ROUTER", "reason": "Relay for mesh backbone"},
        {"node_name": "Node-2", "role": "CLIENT", "reason": "End-user device"},
    ],
    3: [
        {"node_name": "Node-1", "role": "ROUTER", "reason": "Central relay"},
        {"node_name": "Node-2", "role": "ROUTER", "reason": "Coverage extension"},
        {"node_name": "Node-3", "role": "CLIENT", "reason": "End-user device"},
    ],
}


class ProvisioningAdvisor:
    """Generate deployment recommendations for mesh network expansion.

    Usage:
        advisor = ProvisioningAdvisor(db, ollama_client)
        advice = await advisor.recommend({"terrain": "urban", "num_nodes": 5})
    """

    def __init__(self, db: MeshDatabase, ollama: object = None):
        self.db = db
        self._ollama = ollama  # OllamaClient or None

    async def recommend(self, deployment_context: dict) -> dict:
        """Generate deployment recommendations.

        Args:
            deployment_context: Dict with keys:
                - terrain: str (urban, suburban, rural, mountainous, indoor)
                - num_nodes: int
                - power_source: str (battery, solar, mains)
                - desired_coverage_m: float (optional)
                - existing_nodes: list[str] (optional, existing node_ids)

        Returns dict with: summary, recommended_roles, power_settings,
            channel_config, deployment_order, warnings, source.
        """
        # Try Ollama first
        if self._ollama is not None:
            try:
                result = await self._ollama.advise_provisioning(deployment_context)
                if result is not None:
                    return {
                        "summary": result.summary,
                        "recommended_roles": result.recommended_roles,
                        "power_settings": result.power_settings,
                        "channel_config": result.channel_config,
                        "deployment_order": result.deployment_order,
                        "warnings": result.warnings,
                        "source": "ollama",
                    }
            except Exception as exc:
                logger.warning("Ollama provisioning advice failed: %s", exc)

        # Deterministic fallback
        return self._deterministic_advice(deployment_context)

    def _deterministic_advice(self, ctx: dict) -> dict:
        """Generate rule-based deployment advice without AI."""
        num_nodes = ctx.get("num_nodes", 1)
        terrain = ctx.get("terrain", "unknown")
        power_source = ctx.get("power_source", "battery")

        # Role assignment
        if num_nodes in _DEFAULT_ROLES:
            roles = _DEFAULT_ROLES[num_nodes]
        else:
            roles = self._assign_roles_for_count(num_nodes)

        # Power settings based on source
        if power_source == "solar":
            power = "Medium TX power (20 dBm). Conserve for cloudy days."
        elif power_source == "mains":
            power = "Maximum TX power (30 dBm). No battery constraints."
        else:
            power = "Low TX power (10 dBm). Enable power saving mode."

        # Channel config based on terrain
        if terrain in ("urban", "indoor"):
            channel = "Short range, fast data rate. Use LongFast or MediumFast modem preset."
        elif terrain == "mountainous":
            channel = "Long range, slow data rate. Use VeryLongSlow modem preset."
        else:
            channel = "Balanced range/speed. Use LongModerate modem preset."

        # Deployment order
        order = [r["node_name"] for r in roles if r["role"] == "ROUTER"]
        order += [r["node_name"] for r in roles if r["role"] != "ROUTER"]

        # Warnings
        warnings = []
        if num_nodes < 3:
            warnings.append("Fewer than 3 nodes — no mesh redundancy.")
        if terrain == "indoor":
            warnings.append("Indoor deployments have reduced range. Consider repeaters.")
        if power_source == "battery" and num_nodes > 5:
            warnings.append("Large battery-powered fleet requires careful power management.")

        # Existing fleet context
        existing = ctx.get("existing_nodes", [])
        if existing:
            devices = self.db.list_devices()
            existing_count = len(devices)
            warnings.append(f"Existing fleet has {existing_count} nodes. Plan for integration.")

        return {
            "summary": (
                f"Deterministic deployment plan for {num_nodes} nodes "
                f"in {terrain} terrain on {power_source} power."
            ),
            "recommended_roles": roles,
            "power_settings": power,
            "channel_config": channel,
            "deployment_order": order,
            "warnings": warnings,
            "source": "deterministic",
        }

    def _assign_roles_for_count(self, num_nodes: int) -> list[dict]:
        """Generate role assignments for N > 3 nodes."""
        roles = []
        # ~30% routers, rest clients
        num_routers = max(1, num_nodes // 3)
        for i in range(num_nodes):
            if i < num_routers:
                roles.append(
                    {
                        "node_name": f"Node-{i + 1}",
                        "role": "ROUTER",
                        "reason": "Mesh backbone relay",
                    }
                )
            else:
                roles.append(
                    {
                        "node_name": f"Node-{i + 1}",
                        "role": "CLIENT",
                        "reason": "End-user device",
                    }
                )
        return roles

    def get_status(self) -> dict:
        """Get advisor availability info."""
        return {
            "enabled": True,
            "ollama_available": self._ollama is not None,
            "source": "ollama" if self._ollama is not None else "deterministic",
        }
