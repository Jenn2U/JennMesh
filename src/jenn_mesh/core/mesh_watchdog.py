"""Mesh Watchdog — periodic orchestrator for fleet health checks.

Runs as a single background asyncio task, invoking existing detection
methods on staggered intervals.  Each check is independent — a failure
in one does not affect others.  Every invocation is recorded in the
``watchdog_runs`` audit table for operator visibility.

Usage (production — managed by lifespan)::

    watchdog = MeshWatchdog(db=app.state.db)
    task = asyncio.create_task(watchdog_loop_task(watchdog))

Usage (tests)::

    result = watchdog.run_single_cycle()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Optional

from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)

# How often the main loop wakes to evaluate due checks (seconds).
LOOP_SLEEP_SECONDS = 60


class MeshWatchdog:
    """Periodically invokes 9 fleet-health checks and logs results.

    Each check wraps an existing manager method, records the run in the
    ``watchdog_runs`` DB table, and optionally auto-creates / auto-resolves
    alerts when conditions change.

    Constructor Args:
        db:              MeshDatabase instance.
        intervals:       Override per-check interval (seconds).
        thresholds:      Override per-check thresholds (e.g. battery %).
        enabled_checks:  Override per-check enable/disable.
    """

    DEFAULT_INTERVALS: dict[str, int] = {
        "offline_nodes": 120,  # 2 min
        "stale_heartbeats": 120,  # 2 min
        "low_battery": 300,  # 5 min
        "health_scoring": 300,  # 5 min
        "config_drift": 600,  # 10 min
        "topology_spof": 600,  # 10 min
        "failover_recovery": 300,  # 5 min
        "baseline_deviation": 600,  # 10 min
        "post_push_failures": 120,  # 2 min
        "sync_health": 300,  # 5 min
        "encryption_audit": 600,  # 10 min
        "partition_detection": 300,  # 5 min
    }

    DEFAULT_THRESHOLDS: dict[str, Any] = {
        "low_battery_percent": 20,
        "critical_health_score": 50.0,
    }

    def __init__(
        self,
        db: MeshDatabase,
        *,
        intervals: Optional[dict[str, int]] = None,
        thresholds: Optional[dict[str, Any]] = None,
        enabled_checks: Optional[dict[str, bool]] = None,
    ) -> None:
        self.db = db
        self.intervals = {**self.DEFAULT_INTERVALS, **(intervals or {})}
        self.thresholds = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}

        # All checks enabled by default unless overridden
        self.enabled_checks: dict[str, bool] = {name: True for name in self.DEFAULT_INTERVALS}
        if enabled_checks:
            self.enabled_checks.update(enabled_checks)

        # Monotonic timestamps of last successful run per check.
        # Initialised to 0 so every check fires on the first cycle.
        self._last_run: dict[str, float] = {name: 0.0 for name in self.DEFAULT_INTERVALS}

        # Running stats
        self._total_cycles = 0
        self._started_at: Optional[float] = None

        # Map check names → handler methods (bound lazily on first cycle)
        self._check_handlers: dict[str, Callable[[], dict]] = {
            "offline_nodes": self._check_offline_nodes,
            "stale_heartbeats": self._check_stale_heartbeats,
            "low_battery": self._check_low_battery,
            "health_scoring": self._check_health_scoring,
            "config_drift": self._check_config_drift,
            "topology_spof": self._check_topology_spof,
            "failover_recovery": self._check_failover_recovery,
            "baseline_deviation": self._check_baseline_deviation,
            "post_push_failures": self._check_post_push_failures,
            "sync_health": self._check_sync_health,
            "encryption_audit": self._check_encryption_audit,
            "partition_detection": self._check_partition_detection,
        }

    # ── Public API ────────────────────────────────────────────────────

    def run_single_cycle(self) -> dict[str, dict]:
        """Execute one watchdog cycle — run all due checks.

        Returns a dict mapping check_name → result dict for checks that
        actually ran.  Skipped checks (not yet due or disabled) are omitted.
        """
        if self._started_at is None:
            self._started_at = time.monotonic()

        now = time.monotonic()
        results: dict[str, dict] = {}

        for name, handler in self._check_handlers.items():
            if not self.enabled_checks.get(name, False):
                continue
            elapsed = now - self._last_run[name]
            if elapsed < self.intervals[name]:
                continue  # Not yet due

            result = self._run_check(name, handler)
            results[name] = result
            self._last_run[name] = time.monotonic()

        self._total_cycles += 1
        return results

    def get_status(self) -> dict:
        """Return current watchdog status for the API."""
        now = time.monotonic()
        checks_status = {}
        for name in self.DEFAULT_INTERVALS:
            enabled = self.enabled_checks.get(name, False)
            last = self._last_run.get(name, 0.0)
            checks_status[name] = {
                "enabled": enabled,
                "interval_seconds": self.intervals[name],
                "seconds_since_last_run": round(now - last, 1) if last > 0 else None,
            }

        return {
            "enabled": True,
            "total_cycles": self._total_cycles,
            "loop_sleep_seconds": LOOP_SLEEP_SECONDS,
            "checks": checks_status,
            "thresholds": self.thresholds,
        }

    # ── Auto-resolve strategy ─────────────────────────────────────────

    def _auto_resolve_alerts(
        self,
        alert_type: str,
        condition_cleared_fn: Callable[[str], bool],
    ) -> int:
        """Auto-resolve active alerts when the triggering condition clears.

        Uses an aggressive strategy: resolve as soon as condition clears.
        This reduces alert noise for transient issues (battery charges back,
        node reboots, drift is fixed).  Persistent problems will simply
        re-fire on the next watchdog cycle.

        Args:
            alert_type:          AlertType value (e.g. "low_battery").
            condition_cleared_fn: Callable(node_id) → True if the condition
                                  that caused the alert is no longer present.

        Returns:
            Number of alerts resolved.

        TODO: This is a user-contribution point.  See the plan for trade-off
        discussion (aggressive vs conservative vs hysteresis).
        """
        active = self.db.get_active_alerts()
        resolved_count = 0
        for alert in active:
            if alert["alert_type"] != alert_type:
                continue
            node_id = alert["node_id"]
            try:
                if condition_cleared_fn(node_id):
                    self.db.resolve_alert(alert["id"])
                    resolved_count += 1
                    logger.info(
                        "Auto-resolved %s alert #%d for %s",
                        alert_type,
                        alert["id"],
                        node_id,
                    )
            except Exception:
                logger.debug(
                    "Error checking clear condition for alert #%d", alert["id"], exc_info=True
                )
        return resolved_count

    # ── Internal: run a single check with DB audit trail ──────────────

    def _run_check(self, name: str, handler: Callable[[], dict]) -> dict:
        """Execute one check, wrapping with DB audit trail."""
        run_id = self.db.create_watchdog_run(name)
        try:
            result = handler()
            self.db.complete_watchdog_run(
                run_id,
                result_summary=json.dumps(result, default=str),
            )
            return result
        except Exception as exc:
            logger.exception("Watchdog check '%s' failed", name)
            self.db.complete_watchdog_run(run_id, error=str(exc))
            return {"error": str(exc)}

    # ── 8 check implementations ───────────────────────────────────────

    def _check_offline_nodes(self) -> dict:
        """Detect offline nodes and create NODE_OFFLINE / INTERNET_DOWN alerts."""
        from jenn_mesh.core.registry import DeviceRegistry

        registry = DeviceRegistry(self.db)
        alerts = registry.check_offline_nodes()

        # Auto-resolve: if a node is back online, clear its offline alert
        devices = {d["node_id"]: d for d in self.db.list_devices()}
        resolved = self._auto_resolve_alerts(
            "node_offline",
            lambda nid: nid in devices and devices[nid].get("mesh_status") == "reachable",
        )
        resolved += self._auto_resolve_alerts(
            "internet_down",
            lambda nid: nid in devices and devices[nid].get("mesh_status") == "reachable",
        )

        return {
            "new_alerts": len(alerts),
            "auto_resolved": resolved,
        }

    def _check_stale_heartbeats(self) -> dict:
        """Detect stale mesh heartbeats and flip mesh_status."""
        from jenn_mesh.core.heartbeat_receiver import HeartbeatReceiver

        receiver = HeartbeatReceiver(self.db)
        stale_ids = receiver.check_stale_heartbeats()
        return {"stale_nodes": stale_ids, "count": len(stale_ids)}

    def _check_low_battery(self) -> dict:
        """Detect low-battery devices and create LOW_BATTERY alerts."""
        from jenn_mesh.core.registry import DeviceRegistry

        threshold = self.thresholds.get("low_battery_percent", 20)
        registry = DeviceRegistry(self.db)
        alerts = registry.check_low_battery(threshold_percent=threshold)

        # Auto-resolve: if battery recovered above threshold
        devices = {d["node_id"]: d for d in self.db.list_devices()}
        resolved = self._auto_resolve_alerts(
            "low_battery",
            lambda nid: (
                nid in devices
                and devices[nid].get("battery_level") is not None
                and devices[nid]["battery_level"] > threshold
            ),
        )

        return {
            "new_alerts": len(alerts),
            "auto_resolved": resolved,
            "threshold_percent": threshold,
        }

    def _check_health_scoring(self) -> dict:
        """Score fleet health and flag critically unhealthy nodes."""
        from jenn_mesh.core.health_scoring import HealthScorer

        scorer = HealthScorer(self.db)
        breakdowns = scorer.score_fleet()

        threshold = self.thresholds.get("critical_health_score", 50.0)
        critical = [b for b in breakdowns if b.overall_score < threshold]

        return {
            "scored_count": len(breakdowns),
            "critical_count": len(critical),
            "critical_nodes": [b.node_id for b in critical],
            "threshold": threshold,
        }

    def _check_config_drift(self) -> dict:
        """Detect config drift across the fleet."""
        from jenn_mesh.core.config_manager import ConfigManager

        manager = ConfigManager(self.db)
        drifted = manager.get_drift_report()

        # Auto-resolve: if drift cleared (not in drifted list any more)
        drifted_ids = {d["node_id"] for d in drifted}
        resolved = self._auto_resolve_alerts(
            "config_drift",
            lambda nid: nid not in drifted_ids,
        )

        return {
            "drifted_count": len(drifted),
            "drifted_nodes": [d["node_id"] for d in drifted],
            "auto_resolved": resolved,
        }

    def _check_topology_spof(self) -> dict:
        """Find single points of failure in the mesh topology.

        Informational only — no alerts created (no TOPOLOGY_SPOF alert
        type exists).  Operators view SPOFs via the topology API.
        """
        from jenn_mesh.core.topology import TopologyManager

        topo = TopologyManager(self.db)
        spofs = topo.find_single_points_of_failure()
        return {"spof_nodes": spofs, "count": len(spofs)}

    def _check_failover_recovery(self) -> dict:
        """Check if any failed nodes have recovered, auto-revert failovers."""
        from jenn_mesh.core.failover_manager import FailoverManager

        manager = FailoverManager(self.db)
        result = manager.check_recoveries()
        return result

    def _check_baseline_deviation(self) -> dict:
        """Detect nodes deviating from their signal baselines."""
        from jenn_mesh.core.baselines import BaselineManager

        baseline_mgr = BaselineManager(self.db)
        deviations = baseline_mgr.check_fleet_deviations()

        # Auto-resolve: if node is no longer degraded
        degraded_ids = {d.node_id for d in deviations}
        resolved = self._auto_resolve_alerts(
            "baseline_deviation",
            lambda nid: nid not in degraded_ids,
        )

        # Create alerts for newly degraded nodes
        from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType

        new_alerts = 0
        for dev in deviations:
            if not self.db.has_active_alert(dev.node_id, AlertType.BASELINE_DEVIATION.value):
                severity = ALERT_SEVERITY_MAP[AlertType.BASELINE_DEVIATION].value
                msg = f"Baseline deviation: {'; '.join(dev.details)}"
                self.db.create_alert(dev.node_id, AlertType.BASELINE_DEVIATION.value, severity, msg)
                new_alerts += 1

        return {
            "degraded_count": len(deviations),
            "new_alerts": new_alerts,
            "auto_resolved": resolved,
        }

    def _check_post_push_failures(self) -> dict:
        """Check if any config-pushed nodes have gone offline (auto-rollback)."""
        from jenn_mesh.core.config_rollback import ConfigRollbackManager

        manager = ConfigRollbackManager(self.db)
        return manager.check_post_push_failures()

    def _check_sync_health(self) -> dict:
        """Monitor sync relay health — stale sessions, SV divergence, queue depth.

        Creates alerts for sync sessions stuck in 'sending' or 'pending' too long.
        """
        pending = self.db.get_pending_sync_entries()
        stale_count = 0
        for entry in pending:
            # Check if entry has been pending for more than 10 minutes
            created = entry.get("created_at", "")
            if created:
                try:
                    from datetime import datetime, timezone

                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - created_dt).total_seconds()
                    if age > 600:
                        stale_count += 1
                except (ValueError, TypeError):
                    pass

        # Auto-resolve sync_relay_failed alerts if no pending entries remain
        resolved = 0
        if not pending:
            resolved = self._auto_resolve_alerts(
                "sync_relay_failed",
                lambda _nid: True,
            )

        return {
            "pending_queue_depth": len(pending),
            "stale_sessions": stale_count,
            "auto_resolved": resolved,
        }

    def _check_encryption_audit(self) -> dict:
        """Audit fleet encryption posture and flag weak/unencrypted channels."""
        from jenn_mesh.core.encryption_auditor import EncryptionAuditor
        from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType

        auditor = EncryptionAuditor(db=self.db)
        report = auditor.audit_fleet()

        # Create alerts for devices with weak/unencrypted channels
        new_alerts = 0
        weak_node_ids: set[str] = set()
        for device_audit in report.devices:
            if device_audit.weak_channels:
                weak_node_ids.add(device_audit.node_id)
                if not self.db.has_active_alert(
                    device_audit.node_id, AlertType.ENCRYPTION_WEAK.value
                ):
                    severity = ALERT_SEVERITY_MAP[AlertType.ENCRYPTION_WEAK].value
                    weak_names = ", ".join(ch.channel_name for ch in device_audit.weak_channels)
                    msg = f"Weak/unencrypted channels: {weak_names}"
                    self.db.create_alert(
                        device_audit.node_id,
                        AlertType.ENCRYPTION_WEAK.value,
                        severity,
                        msg,
                    )
                    new_alerts += 1

        # Auto-resolve: if a device no longer has weak channels
        resolved = self._auto_resolve_alerts(
            "encryption_weak",
            lambda nid: nid not in weak_node_ids,
        )

        return {
            "fleet_score": report.fleet_score,
            "devices_audited": report.total_devices,
            "weak_device_count": len(weak_node_ids),
            "new_alerts": new_alerts,
            "auto_resolved": resolved,
        }

    def _check_partition_detection(self) -> dict:
        """Detect network partitions — splits and merges in the mesh graph."""
        from jenn_mesh.core.partition_detector import PartitionDetector

        detector = PartitionDetector(db=self.db)
        return detector.check_partitions()


# ── Async loop (started by lifespan) ─────────────────────────────────


async def watchdog_loop_task(watchdog: MeshWatchdog) -> None:
    """Background coroutine — runs watchdog cycles on a timer.

    Same pattern as ``retry_loop_task`` in ``config_queue_manager.py``.
    Uses ``asyncio.to_thread`` because all orchestrated methods are
    synchronous (SQLite + subprocess).
    """
    logger.info("Watchdog loop started (sleep=%ds)", LOOP_SLEEP_SECONDS)
    while True:
        try:
            results = await asyncio.to_thread(watchdog.run_single_cycle)
            if results:
                ran = ", ".join(f"{k}={v}" for k, v in results.items())
                logger.info("Watchdog cycle: %s", ran)
        except Exception:
            logger.exception("Watchdog loop error")
        await asyncio.sleep(LOOP_SLEEP_SECONDS)


def is_watchdog_enabled() -> bool:
    """Check if the watchdog is enabled via environment variable."""
    return os.environ.get("MESH_WATCHDOG_ENABLED", "true").lower() in ("true", "1", "yes")
