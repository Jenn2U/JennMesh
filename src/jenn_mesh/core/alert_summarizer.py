"""Alert summarizer — Ollama-powered alert collapse and summarization.

Collects active alerts, enriches with fleet context, and produces
human-readable summaries. Falls back to rule-based summarization
when Ollama is unavailable.
"""

from __future__ import annotations

import logging
from collections import Counter

from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)


class AlertSummarizer:
    """Collapse and summarize alerts using optional Ollama AI reasoning.

    Usage:
        summarizer = AlertSummarizer(db, ollama_client)
        result = await summarizer.summarize_active()
    """

    def __init__(
        self,
        db: MeshDatabase,
        ollama: object = None,
    ):
        self.db = db
        self._ollama = ollama  # OllamaClient or None

    # ── Public API ───────────────────────────────────────────────────

    async def summarize_active(self) -> dict:
        """Collapse active alerts into a human-readable summary.

        Returns dict with summary text, alert breakdown, and source
        (ollama or rule-based fallback).
        """
        alerts = self.db.get_active_alerts()

        if not alerts:
            return {
                "summary": "No active alerts. Fleet is operating normally.",
                "alert_count": 0,
                "source": "none",
                "breakdown": {},
            }

        breakdown = self._build_breakdown(alerts)

        # Try Ollama first
        ai_summary = None
        if self._ollama is not None:
            try:
                ai_summary = await self._ollama.summarize_alerts(alerts)
            except Exception as exc:
                logger.warning("Ollama alert summarization failed: %s", exc)

        if ai_summary:
            return {
                "summary": ai_summary,
                "alert_count": len(alerts),
                "source": "ollama",
                "breakdown": breakdown,
            }

        # Fallback: rule-based summary
        return {
            "summary": self._rule_based_summary(alerts, breakdown),
            "alert_count": len(alerts),
            "source": "rule-based",
            "breakdown": breakdown,
        }

    async def summarize_for_node(self, node_id: str) -> dict:
        """Per-node alert summary.

        Returns dict with summary text and node-specific alert info.
        """
        alerts = self.db.get_active_alerts(node_id=node_id)

        if not alerts:
            return {
                "node_id": node_id,
                "summary": f"No active alerts for {node_id}.",
                "alert_count": 0,
                "source": "none",
            }

        # Try Ollama
        ai_summary = None
        if self._ollama is not None:
            try:
                ai_summary = await self._ollama.summarize_alerts(alerts)
            except Exception as exc:
                logger.warning(
                    "Ollama node summary failed for %s: %s",
                    node_id.replace("\n", ""),
                    type(exc).__name__,
                )

        if ai_summary:
            return {
                "node_id": node_id,
                "summary": ai_summary,
                "alert_count": len(alerts),
                "source": "ollama",
            }

        # Fallback
        types = [a.get("alert_type", "unknown") for a in alerts]
        type_counts = Counter(types)
        parts = [f"{count} {atype}" for atype, count in type_counts.items()]
        return {
            "node_id": node_id,
            "summary": f"Node {node_id} has {len(alerts)} active alert(s): {', '.join(parts)}.",
            "alert_count": len(alerts),
            "source": "rule-based",
        }

    def get_status(self) -> dict:
        """Get summarizer availability and stats."""
        return {
            "enabled": True,
            "ollama_available": self._ollama is not None,
            "active_alert_count": len(self.db.get_active_alerts()),
        }

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _build_breakdown(alerts: list[dict]) -> dict:
        """Build alert breakdown by type and severity."""
        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        by_node: dict[str, int] = {}

        for alert in alerts:
            atype = alert.get("alert_type", "unknown")
            severity = alert.get("severity", "unknown")
            node_id = alert.get("node_id", "unknown")

            by_type[atype] = by_type.get(atype, 0) + 1
            by_severity[severity] = by_severity.get(severity, 0) + 1
            by_node[node_id] = by_node.get(node_id, 0) + 1

        return {
            "by_type": by_type,
            "by_severity": by_severity,
            "by_node": by_node,
        }

    @staticmethod
    def _rule_based_summary(alerts: list[dict], breakdown: dict) -> str:
        """Generate a rule-based summary when Ollama is unavailable.

        Groups alerts by severity, highlights most critical first.
        """
        count = len(alerts)
        by_severity = breakdown.get("by_severity", {})
        by_type = breakdown.get("by_type", {})
        affected_nodes = len(breakdown.get("by_node", {}))

        parts = [f"{count} active alert(s) across {affected_nodes} node(s)."]

        # Critical/warning counts
        critical = by_severity.get("critical", 0)
        warning = by_severity.get("warning", 0)
        info = by_severity.get("info", 0)

        severity_parts = []
        if critical > 0:
            severity_parts.append(f"{critical} critical")
        if warning > 0:
            severity_parts.append(f"{warning} warning")
        if info > 0:
            severity_parts.append(f"{info} info")

        if severity_parts:
            parts.append(f"Severity breakdown: {', '.join(severity_parts)}.")

        # Most common alert types (top 3)
        sorted_types = sorted(by_type.items(), key=lambda x: x[1], reverse=True)
        if sorted_types:
            top = sorted_types[:3]
            type_parts = [f"{atype} ({c})" for atype, c in top]
            parts.append(f"Top alert types: {', '.join(type_parts)}.")

        return " ".join(parts)
