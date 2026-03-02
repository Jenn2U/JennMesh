"""Tests for radio health scoring (MESH-022)."""

from __future__ import annotations

from datetime import datetime

from jenn_mesh.core.health_scoring import (
    GRADE_HEALTHY_MIN,
    HealthScorer,
    _grade_from_score,
)
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.health import HealthGrade

# ── Grade mapping ────────────────────────────────────────────────────


class TestGradeFromScore:
    def test_healthy(self):
        assert _grade_from_score(100.0) == HealthGrade.HEALTHY
        assert _grade_from_score(80.0) == HealthGrade.HEALTHY

    def test_degraded(self):
        assert _grade_from_score(79.9) == HealthGrade.DEGRADED
        assert _grade_from_score(50.0) == HealthGrade.DEGRADED

    def test_critical(self):
        assert _grade_from_score(49.9) == HealthGrade.CRITICAL
        assert _grade_from_score(0.0) == HealthGrade.CRITICAL


# ── Component scorers via full score_device ──────────────────────────


class TestHealthScorer:
    def test_score_device_not_found(self, db: MeshDatabase):
        scorer = HealthScorer(db)
        assert scorer.score_device("!nonexistent") is None

    def test_healthy_online_device(self, populated_db: MeshDatabase):
        """!aaa11111: online relay, 80% battery, good signal, compatible firmware."""
        scorer = HealthScorer(populated_db)
        result = scorer.score_device("!aaa11111")
        assert result is not None
        assert result.overall_score >= GRADE_HEALTHY_MIN
        assert result.grade == HealthGrade.HEALTHY
        assert result.uptime_score == 100.0  # Online (seen 2 min ago)
        assert result.battery_score == 100.0  # 80% battery
        assert result.firmware_score == 100.0  # heltec_v3 + 2.5.6 = COMPATIBLE

    def test_degraded_battery_device(self, populated_db: MeshDatabase):
        """!ccc33333: offline 2h, 15% battery — should be degraded or critical."""
        scorer = HealthScorer(populated_db)
        result = scorer.score_device("!ccc33333")
        assert result is not None
        assert result.battery_score == 25.0  # 15% battery
        assert result.uptime_score <= 50.0  # Offline 2 hours

    def test_never_seen_device(self, populated_db: MeshDatabase):
        """!ddd44444: sensor, never seen — uptime=0."""
        scorer = HealthScorer(populated_db)
        result = scorer.score_device("!ddd44444")
        assert result is not None
        assert result.uptime_score == 0.0
        assert result.grade in (HealthGrade.DEGRADED, HealthGrade.CRITICAL)

    def test_no_battery_assumes_powered(self, db: MeshDatabase):
        """Device without battery data should get battery_score=100."""
        db.upsert_device("!powered", long_name="Powered", last_seen=datetime.utcnow().isoformat())
        scorer = HealthScorer(db)
        result = scorer.score_device("!powered")
        assert result is not None
        assert result.battery_score == 100.0

    def test_no_signal_data_neutral(self, db: MeshDatabase):
        """Device with no RSSI/SNR should get signal_score=50."""
        db.upsert_device("!nosig", long_name="NoSig", last_seen=datetime.utcnow().isoformat())
        scorer = HealthScorer(db)
        result = scorer.score_device("!nosig")
        assert result is not None
        assert result.signal_score == 50.0

    def test_unknown_firmware_penalized(self, db: MeshDatabase):
        """Device with no explicit hw_model/firmware → UNTESTED (30) or 0."""
        db.upsert_device("!nofw", long_name="NoFW", last_seen=datetime.utcnow().isoformat())
        scorer = HealthScorer(db)
        result = scorer.score_device("!nofw")
        assert result is not None
        # DB may default to "unknown" strings → UNTESTED (30)
        assert result.firmware_score <= 30.0

    def test_compatible_firmware_full_score(self, populated_db: MeshDatabase):
        """Device with known compatible firmware → 100."""
        scorer = HealthScorer(populated_db)
        result = scorer.score_device("!aaa11111")
        assert result is not None
        assert result.firmware_score == 100.0

    def test_signal_vs_baseline(self, populated_db: MeshDatabase):
        """When baseline exists and device is within range, signal should be high."""
        from jenn_mesh.core.baselines import BaselineManager

        manager = BaselineManager(populated_db)
        manager.recompute_baseline("!aaa11111")

        scorer = HealthScorer(populated_db)
        result = scorer.score_device("!aaa11111")
        assert result is not None
        # Signal should be scored against baseline, not absolute
        assert result.signal_score >= 70.0

    def test_config_no_template_neutral(self, db: MeshDatabase):
        """Device with no template assigned → config_score=100."""
        db.upsert_device("!notpl", long_name="NoTemplate", last_seen=datetime.utcnow().isoformat())
        scorer = HealthScorer(db)
        result = scorer.score_device("!notpl")
        assert result is not None
        assert result.config_score == 100.0


# ── Fleet scoring ────────────────────────────────────────────────────


class TestFleetScoring:
    def test_score_fleet(self, populated_db: MeshDatabase):
        scorer = HealthScorer(populated_db)
        scores = scorer.score_fleet()
        assert len(scores) == 4  # 4 devices in populated_db
        node_ids = {s.node_id for s in scores}
        assert "!aaa11111" in node_ids
        assert "!ddd44444" in node_ids

    def test_fleet_summary(self, populated_db: MeshDatabase):
        scorer = HealthScorer(populated_db)
        summary = scorer.fleet_summary()
        assert summary["total"] == 4
        assert summary["healthy"] >= 1  # At least the relay
        assert summary["average_score"] > 0.0
        assert "critical" in summary
        assert "degraded" in summary

    def test_fleet_summary_empty(self, db: MeshDatabase):
        scorer = HealthScorer(db)
        summary = scorer.fleet_summary()
        assert summary["total"] == 0
        assert summary["average_score"] == 0.0

    def test_score_breakdown_model_dump(self, populated_db: MeshDatabase):
        scorer = HealthScorer(populated_db)
        result = scorer.score_device("!aaa11111")
        assert result is not None
        data = result.model_dump()
        assert "overall_score" in data
        assert "grade" in data
        assert "factors" in data
        assert isinstance(data["factors"], dict)
