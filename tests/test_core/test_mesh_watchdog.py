"""Tests for the Mesh Watchdog (MESH-030)."""

from __future__ import annotations

import json
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.core.mesh_watchdog import (
    MeshWatchdog,
    is_watchdog_enabled,
)
from jenn_mesh.db import SCHEMA_VERSION, MeshDatabase

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db() -> MeshDatabase:
    """Fresh test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def watchdog(db: MeshDatabase) -> MeshWatchdog:
    """Watchdog with all intervals set to 0 so every check fires."""
    return MeshWatchdog(
        db=db,
        intervals={name: 0 for name in MeshWatchdog.DEFAULT_INTERVALS},
    )


def _seed_devices(db: MeshDatabase) -> None:
    """Seed a few devices for checks that need them."""
    db.upsert_device("!a", long_name="Node-A", role="CLIENT")
    db.upsert_device("!b", long_name="Relay-B", role="ROUTER")
    with db.connection() as conn:
        for nid in ("!a", "!b"):
            conn.execute(
                "UPDATE devices SET last_seen = datetime('now'),"
                " battery_level = 80, mesh_status = 'reachable'"
                " WHERE node_id = ?",
                (nid,),
            )


# ── Schema v9 watchdog_runs table ────────────────────────────────────


class TestWatchdogRunsDB:
    """Verify schema v9 DB methods."""

    def test_create_and_complete_run(self, db: MeshDatabase) -> None:
        run_id = db.create_watchdog_run("offline_nodes")
        assert isinstance(run_id, int)
        assert run_id > 0

        db.complete_watchdog_run(run_id, result_summary='{"count": 0}')
        runs = db.get_recent_watchdog_runs("offline_nodes")
        assert len(runs) == 1
        assert runs[0]["check_name"] == "offline_nodes"
        assert runs[0]["completed_at"] is not None
        assert runs[0]["result_summary"] == '{"count": 0}'

    def test_complete_run_with_error(self, db: MeshDatabase) -> None:
        run_id = db.create_watchdog_run("low_battery")
        db.complete_watchdog_run(run_id, error="DB locked")
        runs = db.get_recent_watchdog_runs("low_battery")
        assert runs[0]["error"] == "DB locked"
        assert runs[0]["result_summary"] is None

    def test_get_recent_runs_no_filter(self, db: MeshDatabase) -> None:
        db.create_watchdog_run("offline_nodes")
        db.create_watchdog_run("low_battery")
        runs = db.get_recent_watchdog_runs()
        assert len(runs) == 2

    def test_get_recent_runs_with_limit(self, db: MeshDatabase) -> None:
        for _ in range(5):
            db.create_watchdog_run("offline_nodes")
        runs = db.get_recent_watchdog_runs("offline_nodes", limit=3)
        assert len(runs) == 3

    def test_schema_version_current(self, db: MeshDatabase) -> None:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
        assert row["version"] == SCHEMA_VERSION


# ── Constructor / configuration ───────────────────────────────────────


class TestWatchdogConfig:
    """Constructor and configuration."""

    def test_default_intervals(self, db: MeshDatabase) -> None:
        wd = MeshWatchdog(db=db)
        assert wd.intervals["offline_nodes"] == 120
        assert wd.intervals["config_drift"] == 600

    def test_custom_intervals(self, db: MeshDatabase) -> None:
        wd = MeshWatchdog(db=db, intervals={"offline_nodes": 30})
        assert wd.intervals["offline_nodes"] == 30
        # Others keep defaults
        assert wd.intervals["config_drift"] == 600

    def test_custom_thresholds(self, db: MeshDatabase) -> None:
        wd = MeshWatchdog(db=db, thresholds={"low_battery_percent": 30})
        assert wd.thresholds["low_battery_percent"] == 30

    def test_all_checks_enabled_by_default(self, db: MeshDatabase) -> None:
        wd = MeshWatchdog(db=db)
        for name in MeshWatchdog.DEFAULT_INTERVALS:
            assert wd.enabled_checks[name] is True

    def test_disable_specific_check(self, db: MeshDatabase) -> None:
        wd = MeshWatchdog(db=db, enabled_checks={"topology_spof": False})
        assert wd.enabled_checks["topology_spof"] is False
        assert wd.enabled_checks["offline_nodes"] is True


# ── run_single_cycle ──────────────────────────────────────────────────


class TestRunSingleCycle:
    """Test the main cycle method."""

    def test_all_checks_fire_on_first_cycle(self, watchdog: MeshWatchdog) -> None:
        """With intervals=0, every check should fire on the first cycle."""
        with patch.object(
            watchdog,
            "_check_handlers",
            {name: MagicMock(return_value={"ok": True}) for name in MeshWatchdog.DEFAULT_INTERVALS},
        ):
            results = watchdog.run_single_cycle()
        assert len(results) == len(MeshWatchdog.DEFAULT_INTERVALS)
        for name in MeshWatchdog.DEFAULT_INTERVALS:
            assert name in results

    def test_disabled_check_is_skipped(self, db: MeshDatabase) -> None:
        wd = MeshWatchdog(
            db=db,
            intervals={name: 0 for name in MeshWatchdog.DEFAULT_INTERVALS},
            enabled_checks={"topology_spof": False},
        )
        with patch.object(
            wd,
            "_check_handlers",
            {name: MagicMock(return_value={"ok": True}) for name in MeshWatchdog.DEFAULT_INTERVALS},
        ):
            results = wd.run_single_cycle()
        assert "topology_spof" not in results

    def test_interval_respected(self, db: MeshDatabase) -> None:
        """After first cycle, a check with long interval should not re-fire."""
        wd = MeshWatchdog(
            db=db,
            intervals={"offline_nodes": 0, "topology_spof": 99999},
        )
        mock_handler = MagicMock(return_value={"ok": True})
        with patch.object(
            wd,
            "_check_handlers",
            {
                "offline_nodes": mock_handler,
                "topology_spof": mock_handler,
            },
        ):
            # First cycle — both fire
            r1 = wd.run_single_cycle()
            assert "topology_spof" in r1

            # Second cycle — only offline_nodes fires (topo interval not elapsed)
            r2 = wd.run_single_cycle()
            assert "topology_spof" not in r2
            assert "offline_nodes" in r2

    def test_cycle_counter_increments(self, watchdog: MeshWatchdog) -> None:
        with patch.object(watchdog, "_check_handlers", {}):
            assert watchdog._total_cycles == 0
            watchdog.run_single_cycle()
            assert watchdog._total_cycles == 1
            watchdog.run_single_cycle()
            assert watchdog._total_cycles == 2

    def test_check_failure_does_not_block_others(self, db: MeshDatabase) -> None:
        """If one check raises, other checks should still run."""
        wd = MeshWatchdog(
            db=db,
            intervals={name: 0 for name in MeshWatchdog.DEFAULT_INTERVALS},
        )

        def _boom() -> dict:
            raise RuntimeError("kaboom")

        handlers = {
            name: MagicMock(return_value={"ok": True}) for name in MeshWatchdog.DEFAULT_INTERVALS
        }
        handlers["offline_nodes"] = _boom

        with patch.object(wd, "_check_handlers", handlers):
            results = wd.run_single_cycle()

        # offline_nodes should have an error result
        assert "error" in results["offline_nodes"]
        assert "kaboom" in results["offline_nodes"]["error"]

        # All other checks should have succeeded
        for name in MeshWatchdog.DEFAULT_INTERVALS:
            if name != "offline_nodes":
                assert name in results
                assert results[name] == {"ok": True}

    def test_audit_trail_recorded(self, watchdog: MeshWatchdog) -> None:
        """Each check should create a watchdog_runs record."""
        with patch.object(
            watchdog,
            "_check_handlers",
            {
                "offline_nodes": MagicMock(return_value={"count": 2}),
            },
        ):
            watchdog.enabled_checks = {"offline_nodes": True}
            watchdog.run_single_cycle()

        runs = watchdog.db.get_recent_watchdog_runs("offline_nodes")
        assert len(runs) >= 1
        assert runs[0]["completed_at"] is not None
        summary = json.loads(runs[0]["result_summary"])
        assert summary["count"] == 2

    def test_audit_trail_records_error(self, db: MeshDatabase) -> None:
        wd = MeshWatchdog(
            db=db,
            intervals={"offline_nodes": 0},
            enabled_checks={"offline_nodes": True},
        )
        with patch.object(
            wd,
            "_check_handlers",
            {
                "offline_nodes": MagicMock(side_effect=ValueError("test error")),
            },
        ):
            wd.run_single_cycle()

        runs = db.get_recent_watchdog_runs("offline_nodes")
        assert runs[0]["error"] == "test error"
        assert runs[0]["result_summary"] is None


# ── get_status ────────────────────────────────────────────────────────


class TestGetStatus:
    def test_status_structure(self, watchdog: MeshWatchdog) -> None:
        status = watchdog.get_status()
        assert status["enabled"] is True
        assert status["total_cycles"] == 0
        assert "checks" in status
        assert "thresholds" in status
        for name in MeshWatchdog.DEFAULT_INTERVALS:
            assert name in status["checks"]
            assert "enabled" in status["checks"][name]
            assert "interval_seconds" in status["checks"][name]

    def test_status_after_cycle(self, watchdog: MeshWatchdog) -> None:
        with patch.object(watchdog, "_check_handlers", {}):
            watchdog.run_single_cycle()
        status = watchdog.get_status()
        assert status["total_cycles"] == 1


# ── Auto-resolve alerts ──────────────────────────────────────────────


class TestAutoResolveAlerts:
    def test_resolve_when_condition_clears(self, db: MeshDatabase) -> None:
        """Alert should be resolved after hysteresis threshold (2 clear cycles)."""
        _seed_devices(db)
        alert_id = db.create_alert("!a", "low_battery", "warning", "Low battery")
        wd = MeshWatchdog(db=db)

        # First call: clear streak 1/2 — not yet resolved
        resolved = wd._auto_resolve_alerts("low_battery", lambda nid: True)
        assert resolved == 0

        # Second call: clear streak 2/2 → resolves
        resolved = wd._auto_resolve_alerts("low_battery", lambda nid: True)
        assert resolved == 1

        # Verify alert is actually resolved
        active = db.get_active_alerts("!a")
        assert all(a["id"] != alert_id for a in active)

    def test_no_resolve_when_condition_persists(self, db: MeshDatabase) -> None:
        _seed_devices(db)
        db.create_alert("!a", "low_battery", "warning", "Low battery")
        wd = MeshWatchdog(db=db)

        resolved = wd._auto_resolve_alerts("low_battery", lambda nid: False)
        assert resolved == 0

    def test_only_resolves_matching_alert_type(self, db: MeshDatabase) -> None:
        _seed_devices(db)
        db.create_alert("!a", "low_battery", "warning", "Low battery")
        db.create_alert("!a", "node_offline", "critical", "Node offline")
        wd = MeshWatchdog(db=db)

        # Two calls to reach hysteresis threshold for low_battery
        wd._auto_resolve_alerts("low_battery", lambda nid: True)
        resolved = wd._auto_resolve_alerts("low_battery", lambda nid: True)
        assert resolved == 1
        # node_offline should still be active
        active = db.get_active_alerts("!a")
        assert any(a["alert_type"] == "node_offline" for a in active)

    def test_resolve_error_does_not_crash(self, db: MeshDatabase) -> None:
        _seed_devices(db)
        db.create_alert("!a", "low_battery", "warning", "Low battery")
        wd = MeshWatchdog(db=db)

        def _boom(nid: str) -> bool:
            raise RuntimeError("check failed")

        resolved = wd._auto_resolve_alerts("low_battery", _boom)
        assert resolved == 0  # Gracefully skipped


# ── Individual check methods (integration-ish) ────────────────────────


class TestCheckOfflineNodes:
    def test_offline_check_runs(self, watchdog: MeshWatchdog, db: MeshDatabase) -> None:
        _seed_devices(db)
        result = watchdog._check_offline_nodes()
        assert "new_alerts" in result
        assert "auto_resolved" in result


class TestCheckStaleHeartbeats:
    def test_stale_check_runs(self, watchdog: MeshWatchdog, db: MeshDatabase) -> None:
        _seed_devices(db)
        result = watchdog._check_stale_heartbeats()
        assert "stale_nodes" in result
        assert "count" in result


class TestCheckLowBattery:
    def test_low_battery_check_runs(self, watchdog: MeshWatchdog, db: MeshDatabase) -> None:
        _seed_devices(db)
        result = watchdog._check_low_battery()
        assert "new_alerts" in result
        assert "threshold_percent" in result

    def test_low_battery_creates_alert(self, watchdog: MeshWatchdog, db: MeshDatabase) -> None:
        _seed_devices(db)
        # Set battery to 5% (below default 20% threshold)
        with db.connection() as conn:
            conn.execute("UPDATE devices SET battery_level = 5 WHERE node_id = '!a'")
        result = watchdog._check_low_battery()
        assert result["new_alerts"] >= 1


class TestCheckHealthScoring:
    def test_health_scoring_runs(self, watchdog: MeshWatchdog, db: MeshDatabase) -> None:
        _seed_devices(db)
        result = watchdog._check_health_scoring()
        assert "scored_count" in result
        assert "critical_count" in result


class TestCheckConfigDrift:
    def test_config_drift_runs(self, watchdog: MeshWatchdog, db: MeshDatabase) -> None:
        _seed_devices(db)
        result = watchdog._check_config_drift()
        assert "drifted_count" in result
        assert "auto_resolved" in result


class TestCheckTopologySpof:
    def test_topology_spof_runs(self, watchdog: MeshWatchdog, db: MeshDatabase) -> None:
        _seed_devices(db)
        result = watchdog._check_topology_spof()
        assert "spof_nodes" in result
        assert "count" in result


class TestCheckFailoverRecovery:
    def test_failover_recovery_runs(self, watchdog: MeshWatchdog, db: MeshDatabase) -> None:
        result = watchdog._check_failover_recovery()
        assert isinstance(result, dict)


class TestCheckBaselineDeviation:
    def test_baseline_deviation_runs(self, watchdog: MeshWatchdog, db: MeshDatabase) -> None:
        _seed_devices(db)
        result = watchdog._check_baseline_deviation()
        assert "degraded_count" in result
        assert "auto_resolved" in result


# ── is_watchdog_enabled helper ────────────────────────────────────────


class TestIsWatchdogEnabled:
    def test_default_enabled(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert is_watchdog_enabled() is True

    def test_explicit_true(self) -> None:
        with patch.dict("os.environ", {"MESH_WATCHDOG_ENABLED": "true"}):
            assert is_watchdog_enabled() is True

    def test_explicit_false(self) -> None:
        with patch.dict("os.environ", {"MESH_WATCHDOG_ENABLED": "false"}):
            assert is_watchdog_enabled() is False

    def test_numeric_one(self) -> None:
        with patch.dict("os.environ", {"MESH_WATCHDOG_ENABLED": "1"}):
            assert is_watchdog_enabled() is True

    def test_numeric_zero(self) -> None:
        with patch.dict("os.environ", {"MESH_WATCHDOG_ENABLED": "0"}):
            assert is_watchdog_enabled() is False
