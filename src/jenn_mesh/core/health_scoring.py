"""Radio health scoring — composite 0-100 per-node health scores (MESH-022)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from jenn_mesh.core.baselines import BaselineManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.health import HealthGrade, HealthScoreBreakdown
from jenn_mesh.provisioning.firmware import COMPATIBLE, FirmwareTracker

# ── Score weights (must sum to 1.0) ──────────────────────────────────
WEIGHT_UPTIME = 0.30
WEIGHT_SIGNAL = 0.25
WEIGHT_BATTERY = 0.20
WEIGHT_CONFIG = 0.15
WEIGHT_FIRMWARE = 0.10

# ── Grade thresholds ─────────────────────────────────────────────────
GRADE_HEALTHY_MIN = 80.0
GRADE_DEGRADED_MIN = 50.0

# ── Uptime parameters ────────────────────────────────────────────────
ONLINE_THRESHOLD_MINUTES = 15


class HealthScorer:
    """Computes composite health scores per device from 5 weighted factors."""

    def __init__(self, db: MeshDatabase):
        self._db = db
        self._baseline_mgr = BaselineManager(db)
        self._firmware_tracker = FirmwareTracker(db)

    def score_device(self, node_id: str) -> Optional[HealthScoreBreakdown]:
        """Compute full health score breakdown for a single device."""
        device = self._db.get_device(node_id)
        if device is None:
            return None

        factors: dict[str, str] = {}

        uptime = self._score_uptime(device, factors)
        signal = self._score_signal(device, factors)
        battery = self._score_battery(device, factors)
        config = self._score_config(device, factors)
        firmware = self._score_firmware(device, factors)

        overall = (
            uptime * WEIGHT_UPTIME
            + signal * WEIGHT_SIGNAL
            + battery * WEIGHT_BATTERY
            + config * WEIGHT_CONFIG
            + firmware * WEIGHT_FIRMWARE
        )

        grade = _grade_from_score(overall)

        return HealthScoreBreakdown(
            node_id=node_id,
            overall_score=round(overall, 1),
            grade=grade,
            uptime_score=round(uptime, 1),
            signal_score=round(signal, 1),
            battery_score=round(battery, 1),
            config_score=round(config, 1),
            firmware_score=round(firmware, 1),
            factors=factors,
        )

    def score_fleet(self) -> list[HealthScoreBreakdown]:
        """Score all devices in the fleet."""
        devices = self._db.list_devices()
        scores = []
        for device in devices:
            result = self.score_device(device["node_id"])
            if result is not None:
                scores.append(result)
        return scores

    def fleet_summary(self) -> dict:
        """Aggregate health statistics across the fleet."""
        scores = self.score_fleet()
        if not scores:
            return {
                "total": 0,
                "healthy": 0,
                "degraded": 0,
                "critical": 0,
                "average_score": 0.0,
            }

        healthy = sum(1 for s in scores if s.grade == HealthGrade.HEALTHY)
        degraded = sum(1 for s in scores if s.grade == HealthGrade.DEGRADED)
        critical = sum(1 for s in scores if s.grade == HealthGrade.CRITICAL)
        avg = sum(s.overall_score for s in scores) / len(scores)

        return {
            "total": len(scores),
            "healthy": healthy,
            "degraded": degraded,
            "critical": critical,
            "average_score": round(avg, 1),
        }

    # ── Component scorers ────────────────────────────────────────────

    def _score_uptime(self, device: dict, factors: dict[str, str]) -> float:
        """0-100 based on how recently the device was seen."""
        last_seen = device.get("last_seen")
        if not last_seen:
            factors["uptime"] = "Never seen"
            return 0.0

        try:
            ts = datetime.fromisoformat(last_seen)
        except (ValueError, TypeError):
            factors["uptime"] = "Invalid timestamp"
            return 0.0

        elapsed = datetime.utcnow() - ts
        minutes = elapsed.total_seconds() / 60.0

        if minutes <= ONLINE_THRESHOLD_MINUTES:
            factors["uptime"] = "Online"
            return 100.0
        elif minutes <= 60:
            factors["uptime"] = f"Seen {int(minutes)}m ago"
            return 80.0
        elif minutes <= 360:
            factors["uptime"] = f"Seen {int(minutes / 60)}h ago"
            return 50.0
        elif minutes <= 1440:
            factors["uptime"] = f"Seen {int(minutes / 60)}h ago"
            return 25.0
        else:
            days = int(minutes / 1440)
            factors["uptime"] = f"Offline {days}d"
            return 0.0

    def _score_signal(self, device: dict, factors: dict[str, str]) -> float:
        """0-100 based on deviation from baseline (or absolute if no baseline)."""
        baseline = self._baseline_mgr.get_baseline(device["node_id"])

        if baseline is not None and baseline.has_sufficient_data:
            return self._score_signal_vs_baseline(device, baseline, factors)

        # No baseline — use absolute RSSI heuristic
        rssi = device.get("signal_rssi")
        if rssi is None:
            factors["signal"] = "No signal data (neutral)"
            return 50.0

        if rssi >= -70:
            factors["signal"] = f"Excellent RSSI ({rssi})"
            return 100.0
        elif rssi >= -85:
            factors["signal"] = f"Good RSSI ({rssi})"
            return 80.0
        elif rssi >= -100:
            factors["signal"] = f"Fair RSSI ({rssi})"
            return 60.0
        elif rssi >= -110:
            factors["signal"] = f"Weak RSSI ({rssi})"
            return 30.0
        else:
            factors["signal"] = f"Very weak RSSI ({rssi})"
            return 10.0

    def _score_signal_vs_baseline(self, device: dict, baseline, factors: dict[str, str]) -> float:
        """Score signal quality relative to the node's own baseline."""
        rssi = device.get("signal_rssi")
        if rssi is None or baseline.rssi_mean is None or baseline.rssi_stddev is None:
            factors["signal"] = "Baseline available but no current RSSI"
            return 50.0

        if baseline.rssi_stddev == 0:
            factors["signal"] = "Baseline stddev=0, using absolute"
            return 80.0 if rssi >= -100 else 40.0

        sigma = (rssi - baseline.rssi_mean) / baseline.rssi_stddev

        if abs(sigma) <= 1.0:
            factors["signal"] = f"Within 1σ of baseline ({sigma:+.1f}σ)"
            return 100.0
        elif abs(sigma) <= 2.0:
            factors["signal"] = f"Within 2σ of baseline ({sigma:+.1f}σ)"
            return 70.0
        elif abs(sigma) <= 3.0:
            factors["signal"] = f"Outside 2σ ({sigma:+.1f}σ)"
            return 40.0
        else:
            factors["signal"] = f"Far from baseline ({sigma:+.1f}σ)"
            return 10.0

    def _score_battery(self, device: dict, factors: dict[str, str]) -> float:
        """0-100 based on battery level."""
        level = device.get("battery_level")
        if level is None:
            factors["battery"] = "No battery data (assume powered)"
            return 100.0

        if level >= 80:
            factors["battery"] = f"Good ({level}%)"
            return 100.0
        elif level >= 50:
            factors["battery"] = f"Moderate ({level}%)"
            return 80.0
        elif level >= 30:
            factors["battery"] = f"Low ({level}%)"
            return 50.0
        elif level >= 15:
            factors["battery"] = f"Very low ({level}%)"
            return 25.0
        else:
            factors["battery"] = f"Critical ({level}%)"
            return 5.0

    def _score_config(self, device: dict, factors: dict[str, str]) -> float:
        """0-100 based on config drift status."""
        config_hash = device.get("config_hash")
        template_hash = device.get("template_hash")

        if not template_hash:
            factors["config"] = "No template assigned (neutral)"
            return 100.0

        if not config_hash:
            factors["config"] = "No config reported"
            return 50.0

        if config_hash == template_hash:
            factors["config"] = "Config matches template"
            return 100.0
        else:
            factors["config"] = "Config drift detected"
            return 20.0

    def _score_firmware(self, device: dict, factors: dict[str, str]) -> float:
        """0-100 based on firmware compatibility status."""
        hw_model = device.get("hw_model")
        fw_version = device.get("firmware_version")

        if not hw_model or not fw_version:
            factors["firmware"] = "Unknown firmware/hardware"
            return 0.0

        status = self._firmware_tracker.check_compatibility(hw_model, fw_version)
        if status == COMPATIBLE:
            factors["firmware"] = f"Compatible ({hw_model}/{fw_version})"
            return 100.0
        elif status == "INCOMPATIBLE":
            factors["firmware"] = f"Incompatible ({hw_model}/{fw_version})"
            return 0.0
        else:
            factors["firmware"] = f"Untested ({hw_model}/{fw_version})"
            return 30.0


def _grade_from_score(score: float) -> HealthGrade:
    """Map a 0-100 score to a health grade."""
    if score >= GRADE_HEALTHY_MIN:
        return HealthGrade.HEALTHY
    elif score >= GRADE_DEGRADED_MIN:
        return HealthGrade.DEGRADED
    else:
        return HealthGrade.CRITICAL
