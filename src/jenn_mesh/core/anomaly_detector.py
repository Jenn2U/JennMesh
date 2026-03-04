"""Anomaly detector — Ollama-powered telemetry anomaly analysis.

Combines statistical baseline deviation detection with LLM reasoning.
When Ollama is unavailable, falls back to baseline-only detection.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from jenn_mesh.core.baselines import BaselineManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """Detect anomalies in mesh node telemetry using baselines + optional Ollama.

    Usage:
        detector = AnomalyDetector(db, ollama_client, baseline_mgr)
        report = await detector.analyze_node("!aaa11111")
    """

    def __init__(
        self,
        db: MeshDatabase,
        ollama: object = None,
        baseline_mgr: Optional[BaselineManager] = None,
    ):
        self.db = db
        self._ollama = ollama  # OllamaClient or None
        self._baseline = baseline_mgr or BaselineManager(db)

    # ── Public API ───────────────────────────────────────────────────

    async def analyze_node(self, node_id: str) -> Optional[dict]:
        """Analyze recent telemetry for anomalies.

        Checks baseline deviations first, then optionally enriches with
        Ollama reasoning if available and anomaly detected.

        Returns dict with anomaly report or None if no anomaly.
        """
        # Check for baseline deviations
        deviation = self._baseline.check_deviation(node_id)
        if deviation is None:
            return None

        is_deviant = len(deviation.deviating_metrics) > 0
        if not is_deviant:
            return None

        # Build report from baseline deviation
        report = {
            "node_id": node_id,
            "is_anomalous": True,
            "source": "baseline",
            "deviating_metrics": deviation.deviating_metrics,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ai_analysis": None,
        }

        # Enrich with Ollama if available
        if self._ollama is not None:
            try:
                context = self.get_telemetry_context(node_id)
                context["deviations"] = deviation.deviating_metrics
                ollama_result = await self._ollama.analyze_anomaly(context)
                if ollama_result is not None:
                    report["ai_analysis"] = {
                        "summary": ollama_result.summary,
                        "severity": ollama_result.severity,
                        "recommended_action": ollama_result.recommended_action,
                        "confidence": ollama_result.confidence,
                    }
                    report["source"] = "baseline+ollama"
            except Exception as exc:
                logger.warning("Ollama anomaly analysis failed: %s", exc)

        # Create alert in DB
        alert_severity = ALERT_SEVERITY_MAP[AlertType.ANOMALY_DETECTED].value
        self.db.create_alert(
            node_id=node_id,
            alert_type=AlertType.ANOMALY_DETECTED.value,
            severity=alert_severity,
            message=(
                f"Anomaly detected on {node_id}: " f"{', '.join(deviation.deviating_metrics)}"
            ),
        )

        return report

    async def analyze_fleet(self) -> list[dict]:
        """Analyze all nodes that show baseline deviations.

        Returns list of anomaly reports for deviant nodes.
        """
        devices = self.db.list_devices()
        reports: list[dict] = []

        for device in devices:
            node_id = device.get("node_id") if isinstance(device, dict) else device
            try:
                report = await self.analyze_node(node_id)
                if report is not None:
                    reports.append(report)
            except Exception as exc:
                logger.warning(
                    "Fleet anomaly analysis failed for %s: %s",
                    node_id.replace("\n", ""),
                    type(exc).__name__,
                )

        return reports

    def get_telemetry_context(self, node_id: str) -> dict:
        """Build context dict for Ollama prompt.

        Includes recent telemetry samples, baseline snapshot, and device info.
        """
        # Get device info
        device = self.db.get_device(node_id)
        device_info = {}
        if device:
            device_info = {
                "node_id": node_id,
                "long_name": device.get("long_name"),
                "role": device.get("role"),
                "battery_level": device.get("battery_level"),
                "last_seen": device.get("last_seen"),
            }

        # Get recent telemetry (all history, slice last 20)
        samples = self.db.get_telemetry_history(node_id)

        # Get baseline
        baseline = self._baseline.recompute_baseline(node_id)
        baseline_info = None
        if baseline:
            baseline_info = {
                "rssi_mean": baseline.rssi_mean,
                "rssi_stddev": baseline.rssi_stddev,
                "snr_mean": baseline.snr_mean,
                "snr_stddev": baseline.snr_stddev,
                "battery_drain_rate": baseline.battery_drain_rate,
                "sample_count": baseline.sample_count,
            }

        return {
            "device": device_info,
            "recent_samples": samples[-10:] if samples else [],
            "baseline": baseline_info,
        }

    def get_history(self, limit: int = 50) -> list[dict]:
        """Get recent anomaly-related alerts."""
        all_alerts = self.db.get_active_alerts()
        anomaly_alerts = [
            a for a in all_alerts if a.get("alert_type") == AlertType.ANOMALY_DETECTED.value
        ]
        return anomaly_alerts[:limit]

    def get_status(self) -> dict:
        """Get detector availability and stats."""
        ollama_available = self._ollama is not None
        return {
            "enabled": True,
            "ollama_available": ollama_available,
            "baseline_window_days": self._baseline._window_days,
            "deviation_threshold": self._baseline._deviation_threshold,
        }
