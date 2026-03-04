"""Lost node reasoner — Ollama-powered predictive location analysis.

Extends the existing LostNodeFinder with AI-driven reasoning about
where a lost node might be, based on historical movement patterns,
battery state, environmental conditions, and nearby node topology.

When Ollama is unavailable, falls back to deterministic heuristics.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)


class LostNodeReasoner:
    """Generate AI-powered reasoning about a lost node's probable location.

    Usage:
        reasoner = LostNodeReasoner(db, ollama_client)
        result = await reasoner.reason("!ccc33333")
    """

    def __init__(self, db: MeshDatabase, ollama: object = None):
        self.db = db
        self._ollama = ollama  # OllamaClient or None

    async def reason(self, node_id: str) -> dict:
        """Generate probabilistic location reasoning for a lost node.

        Returns dict with: node_id, probable_location, reasoning,
            search_recommendations, confidence, source, context.
        """
        context = self._build_context(node_id)

        # Try Ollama first
        if self._ollama is not None:
            try:
                result = await self._ollama.reason_lost_node(context)
                if result is not None:
                    return {
                        "node_id": node_id,
                        "probable_location": result.probable_location,
                        "reasoning": result.reasoning,
                        "search_recommendations": result.search_recommendations,
                        "confidence": result.confidence,
                        "source": "ollama",
                        "context": context,
                    }
            except Exception as exc:
                logger.warning("Ollama lost node reasoning failed: %s", exc)

        # Deterministic fallback
        return self._deterministic_reasoning(node_id, context)

    def _build_context(self, node_id: str) -> dict:
        """Build context dict for reasoning (used by both AI and deterministic)."""
        device = self.db.get_device(node_id)
        device_info = {}
        if device:
            device_info = {
                "node_id": node_id,
                "long_name": device.get("long_name"),
                "role": device.get("role"),
                "battery_level": device.get("battery_level"),
                "last_seen": device.get("last_seen"),
                "hw_model": device.get("hw_model"),
            }

        # Last N positions — query directly since DB only has get_latest_position
        last_positions = []
        try:
            with self.db.connection() as conn:
                rows = conn.execute(
                    """SELECT latitude, longitude, altitude, timestamp
                       FROM positions WHERE node_id = ?
                       ORDER BY timestamp DESC LIMIT 10""",
                    (node_id,),
                ).fetchall()
                last_positions = [dict(r) for r in rows]
        except Exception:
            pass

        # Time since last contact
        time_since = None
        last_seen = device_info.get("last_seen") if device_info else None
        if last_seen:
            try:
                ls_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                if ls_dt.tzinfo is None:
                    ls_dt = ls_dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                time_since = round((now - ls_dt).total_seconds() / 3600, 1)
            except (ValueError, TypeError):
                pass

        # Nearby nodes from topology edges
        nearby = []
        try:
            edges = self.db.get_edges_for_node(node_id)
            nearby = [
                {
                    "node_id": (
                        e.get("to_node") if e.get("from_node") == node_id else e.get("from_node")
                    ),
                    "snr": e.get("snr"),
                    "rssi": e.get("rssi"),
                }
                for e in edges
            ]
        except Exception:
            pass

        return {
            "node_id": node_id,
            "device": device_info,
            "last_positions": last_positions,
            "battery_at_last_contact": device_info.get("battery_level"),
            "time_since_last_contact_hours": time_since,
            "nearby_nodes": nearby,
        }

    def _deterministic_reasoning(self, node_id: str, context: dict) -> dict:
        """Generate rule-based location reasoning without AI."""
        device = context.get("device", {})
        positions = context.get("last_positions", [])
        battery = context.get("battery_at_last_contact")
        time_since = context.get("time_since_last_contact_hours")

        # Build reasoning
        reasoning_parts = []
        recommendations = []
        confidence = "low"

        # Position-based reasoning
        if positions:
            last = positions[0]
            lat = last.get("latitude")
            lon = last.get("longitude")
            if lat and lon:
                probable_location = f"Near last known position ({lat:.4f}, {lon:.4f})"
                reasoning_parts.append(f"Last GPS fix at ({lat:.4f}, {lon:.4f}).")
                recommendations.append(f"Search within 500m of ({lat:.4f}, {lon:.4f}).")
                confidence = "medium"

                # Movement vector analysis
                if len(positions) >= 2:
                    prev = positions[1]
                    plat = prev.get("latitude")
                    plon = prev.get("longitude")
                    if plat and plon:
                        dlat = lat - plat
                        dlon = lon - plon
                        if abs(dlat) > 0.0001 or abs(dlon) > 0.0001:
                            direction = self._compass_direction(dlat, dlon)
                            reasoning_parts.append(f"Movement trend: {direction}.")
                            recommendations.append(f"Extend search {direction} from last position.")
            else:
                probable_location = "Unknown — no GPS data available"
                reasoning_parts.append("No GPS coordinates in position history.")
        else:
            probable_location = "Unknown — no position history"
            reasoning_parts.append("No position history available for this node.")
            recommendations.append("Check node's physical deployment location in fleet records.")

        # Battery reasoning
        if battery is not None:
            if battery < 10:
                reasoning_parts.append(
                    f"Battery was critically low ({battery}%) at last contact — "
                    "likely powered off."
                )
                recommendations.append("Node may have shut down. Check power source.")
            elif battery < 30:
                reasoning_parts.append(f"Battery was low ({battery}%) — limited remaining runtime.")

        # Time-based reasoning
        if time_since is not None:
            if time_since > 48:
                reasoning_parts.append(
                    f"Node has been offline for {time_since:.0f} hours — "
                    "extended absence suggests hardware failure or relocation."
                )
                confidence = "low"
            elif time_since > 12:
                reasoning_parts.append(f"Node offline for {time_since:.0f} hours.")

        # Role-based reasoning
        role = device.get("role", "")
        if role in ("ROUTER", "ROUTER_CLIENT"):
            reasoning_parts.append("This is a relay node — typically stationary.")
            recommendations.append("Check the fixed deployment location.")
            if confidence == "medium":
                confidence = "medium"
        elif role == "CLIENT":
            reasoning_parts.append("This is a mobile client — may have moved.")

        return {
            "node_id": node_id,
            "probable_location": probable_location,
            "reasoning": " ".join(reasoning_parts),
            "search_recommendations": recommendations,
            "confidence": confidence,
            "source": "deterministic",
            "context": context,
        }

    @staticmethod
    def _compass_direction(dlat: float, dlon: float) -> str:
        """Convert lat/lon delta to compass direction."""
        if abs(dlat) > abs(dlon):
            return "north" if dlat > 0 else "south"
        else:
            return "east" if dlon > 0 else "west"

    def get_status(self) -> dict:
        """Get reasoner availability info."""
        return {
            "enabled": True,
            "ollama_available": self._ollama is not None,
            "source": "ollama" if self._ollama is not None else "deterministic",
        }
