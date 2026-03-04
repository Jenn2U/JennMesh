"""Tests for the AnomalyDetector (MESH-017)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jenn_mesh.core.anomaly_detector import AnomalyDetector
from jenn_mesh.core.baselines import BaselineManager
from jenn_mesh.db import MeshDatabase

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db() -> MeshDatabase:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def baseline_mgr(db: MeshDatabase) -> BaselineManager:
    return BaselineManager(db)


@pytest.fixture
def detector(db: MeshDatabase, baseline_mgr: BaselineManager) -> AnomalyDetector:
    return AnomalyDetector(db=db, baseline_mgr=baseline_mgr)


def _seed_node(db: MeshDatabase, node_id: str = "!aaa11111") -> None:
    """Seed a device and enough telemetry for baseline computation."""
    db.upsert_device(node_id, long_name="TestNode", role="CLIENT")
    now = datetime.utcnow()
    for i in range(25):
        ts = (now - timedelta(days=5, hours=i)).isoformat()
        db.add_telemetry_sample(
            node_id,
            rssi=-85 + (i % 3),
            snr=10.0 + (i % 4) * 0.5,
            battery_level=80 - i,
            voltage=4.0 - i * 0.01,
            timestamp=ts,
        )


# ── Constructor ──────────────────────────────────────────────────────


class TestDetectorInit:
    def test_init_without_ollama(self, db: MeshDatabase) -> None:
        detector = AnomalyDetector(db=db)
        assert detector._ollama is None
        assert detector._baseline is not None

    def test_init_with_ollama(self, db: MeshDatabase) -> None:
        mock_ollama = MagicMock()
        detector = AnomalyDetector(db=db, ollama=mock_ollama)
        assert detector._ollama is mock_ollama


# ── analyze_node ─────────────────────────────────────────────────────


class TestAnalyzeNode:
    @pytest.mark.asyncio
    async def test_no_anomaly_insufficient_data(self, detector: AnomalyDetector) -> None:
        """No telemetry → no baseline → no anomaly."""
        result = await detector.analyze_node("!no_data")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_anomaly_within_baseline(
        self, detector: AnomalyDetector, db: MeshDatabase
    ) -> None:
        """Node with normal telemetry should not trigger anomaly."""
        _seed_node(db)
        # Patch check_deviation to return no deviations
        with patch.object(
            detector._baseline,
            "check_deviation",
            return_value=MagicMock(deviating_metrics=[]),
        ):
            result = await detector.analyze_node("!aaa11111")
        assert result is None

    @pytest.mark.asyncio
    async def test_anomaly_detected_baseline_only(
        self, detector: AnomalyDetector, db: MeshDatabase
    ) -> None:
        """Baseline deviation triggers anomaly without Ollama."""
        _seed_node(db)
        mock_deviation = MagicMock(deviating_metrics=["rssi", "snr"])
        with patch.object(detector._baseline, "check_deviation", return_value=mock_deviation):
            result = await detector.analyze_node("!aaa11111")

        assert result is not None
        assert result["is_anomalous"] is True
        assert result["source"] == "baseline"
        assert "rssi" in result["deviating_metrics"]
        assert result["ai_analysis"] is None

    @pytest.mark.asyncio
    async def test_anomaly_creates_alert(self, detector: AnomalyDetector, db: MeshDatabase) -> None:
        """Anomaly detection should create a DB alert."""
        _seed_node(db)
        mock_deviation = MagicMock(deviating_metrics=["battery"])
        with patch.object(detector._baseline, "check_deviation", return_value=mock_deviation):
            await detector.analyze_node("!aaa11111")

        alerts = db.get_active_alerts("!aaa11111")
        anomaly_alerts = [a for a in alerts if a["alert_type"] == "anomaly_detected"]
        assert len(anomaly_alerts) == 1
        assert "battery" in anomaly_alerts[0]["message"]

    @pytest.mark.asyncio
    async def test_anomaly_with_ollama(self, db: MeshDatabase) -> None:
        """When Ollama available, AI analysis enriches the report."""
        _seed_node(db)
        mock_ollama = AsyncMock()
        mock_ollama.analyze_anomaly = AsyncMock(
            return_value=MagicMock(
                summary="Battery drain anomaly",
                severity="warning",
                recommended_action="Check power supply",
                confidence=0.85,
            )
        )
        detector = AnomalyDetector(db=db, ollama=mock_ollama)

        mock_deviation = MagicMock(deviating_metrics=["battery"])
        with patch.object(detector._baseline, "check_deviation", return_value=mock_deviation):
            result = await detector.analyze_node("!aaa11111")

        assert result["source"] == "baseline+ollama"
        assert result["ai_analysis"] is not None
        assert result["ai_analysis"]["summary"] == "Battery drain anomaly"
        assert result["ai_analysis"]["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_ollama_failure_degrades_gracefully(self, db: MeshDatabase) -> None:
        """If Ollama fails, report still has baseline data."""
        _seed_node(db)
        mock_ollama = AsyncMock()
        mock_ollama.analyze_anomaly = AsyncMock(side_effect=RuntimeError("Ollama down"))
        detector = AnomalyDetector(db=db, ollama=mock_ollama)

        mock_deviation = MagicMock(deviating_metrics=["rssi"])
        with patch.object(detector._baseline, "check_deviation", return_value=mock_deviation):
            result = await detector.analyze_node("!aaa11111")

        assert result is not None
        assert result["source"] == "baseline"
        assert result["ai_analysis"] is None


# ── analyze_fleet ────────────────────────────────────────────────────


class TestAnalyzeFleet:
    @pytest.mark.asyncio
    async def test_empty_fleet(self, detector: AnomalyDetector) -> None:
        reports = await detector.analyze_fleet()
        assert reports == []

    @pytest.mark.asyncio
    async def test_fleet_with_anomaly(self, detector: AnomalyDetector, db: MeshDatabase) -> None:
        _seed_node(db, "!aaa11111")
        _seed_node(db, "!bbb22222")

        mock_deviation = MagicMock(deviating_metrics=["rssi"])
        with patch.object(detector._baseline, "check_deviation", return_value=mock_deviation):
            reports = await detector.analyze_fleet()

        assert len(reports) == 2

    @pytest.mark.asyncio
    async def test_fleet_error_skips_node(
        self, detector: AnomalyDetector, db: MeshDatabase
    ) -> None:
        """If one node fails, other nodes should still be analyzed."""
        _seed_node(db, "!aaa11111")
        _seed_node(db, "!bbb22222")

        call_count = 0

        async def _analyze_side_effect(node_id):
            nonlocal call_count
            call_count += 1
            if node_id == "!aaa11111":
                raise RuntimeError("test error")
            return {"node_id": node_id, "is_anomalous": True}

        with patch.object(detector, "analyze_node", side_effect=_analyze_side_effect):
            reports = await detector.analyze_fleet()

        # Only !bbb22222 should succeed
        assert len(reports) == 1


# ── Telemetry context ────────────────────────────────────────────────


class TestTelemetryContext:
    def test_context_with_data(self, detector: AnomalyDetector, db: MeshDatabase) -> None:
        _seed_node(db)
        context = detector.get_telemetry_context("!aaa11111")
        assert "device" in context
        assert "recent_samples" in context
        assert "baseline" in context
        assert context["device"]["node_id"] == "!aaa11111"

    def test_context_empty_node(self, detector: AnomalyDetector) -> None:
        context = detector.get_telemetry_context("!nonexistent")
        assert context["device"] == {}
        assert context["recent_samples"] == []


# ── Status & History ─────────────────────────────────────────────────


class TestDetectorStatus:
    def test_status_without_ollama(self, detector: AnomalyDetector) -> None:
        status = detector.get_status()
        assert status["enabled"] is True
        assert status["ollama_available"] is False

    def test_status_with_ollama(self, db: MeshDatabase) -> None:
        detector = AnomalyDetector(db=db, ollama=MagicMock())
        status = detector.get_status()
        assert status["ollama_available"] is True


class TestDetectorHistory:
    def test_history_empty(self, detector: AnomalyDetector) -> None:
        history = detector.get_history()
        assert history == []

    def test_history_with_alerts(self, detector: AnomalyDetector, db: MeshDatabase) -> None:
        db.create_alert("!aaa11111", "anomaly_detected", "warning", "Test anomaly")
        db.create_alert("!aaa11111", "low_battery", "warning", "Not an anomaly")

        history = detector.get_history()
        assert len(history) == 1
        assert history[0]["alert_type"] == "anomaly_detected"
