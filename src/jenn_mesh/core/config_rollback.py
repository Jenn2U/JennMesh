"""OTA Config Rollback — snapshot, monitor, and auto-rollback config pushes.

Wraps every config push (bulk or drift-remediation) with a safety net:
 1. **Snapshot** the device's current YAML config before pushing.
 2. **Monitor** the node for N minutes after the push completes.
 3. **Auto-rollback** if the node goes offline during the monitoring window.
 4. **Confirm** if the node survives the monitoring window.

Integration points:
    - ``BulkPushManager._execute_push()`` — calls ``snapshot_before_push()``
    - ``DriftRemediationManager.remediate_device()`` — same pattern
    - ``MeshWatchdog._check_post_push_failures()`` — calls ``check_post_push_failures()``

FailoverManager is excluded — it already has its own original_value/revert
pattern in ``failover_compensations``.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional

from jenn_mesh.agent.remote_admin import RemoteAdmin
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType

logger = logging.getLogger(__name__)

# How long to wait after a push before evaluating node health.
DEFAULT_MONITORING_MINUTES = 10

# Reuse a snapshot if one already exists from < this many minutes ago.
SNAPSHOT_REUSE_MINUTES = 5


class ConfigRollbackManager:
    """Manage OTA config rollback: snapshot → push → monitor → rollback/confirm.

    Constructor Args:
        db:                 MeshDatabase instance.
        admin_port:         Serial port for ``RemoteAdmin`` (default "auto").
        monitoring_minutes: Minutes to monitor after push (default 10).
    """

    def __init__(
        self,
        db: MeshDatabase,
        *,
        admin_port: str = "auto",
        monitoring_minutes: Optional[int] = None,
    ) -> None:
        self.db = db
        self._admin = RemoteAdmin(port=admin_port)
        self.monitoring_minutes = monitoring_minutes or DEFAULT_MONITORING_MINUTES

    # ── Pre-push snapshot ─────────────────────────────────────────────

    def snapshot_before_push(
        self,
        node_id: str,
        push_source: str,
    ) -> Optional[int]:
        """Take config snapshot before a push. Returns snapshot_id or None on failure.

        Optimisation: if a snapshot for this node exists from < 5 minutes ago
        with ``yaml_before`` populated, reuse it (avoids a 30-120s mesh
        round-trip for rapid re-pushes to the same node).
        """
        # Check for reusable recent snapshot
        recent = self.db.get_snapshots_for_node(node_id, limit=1)
        if recent:
            snap = recent[0]
            if snap["yaml_before"] and snap["status"] in (
                "active",
                "push_failed",
                "snapshot_failed",
            ):
                created = datetime.fromisoformat(snap["created_at"])
                cutoff = datetime.now(timezone.utc) - timedelta(minutes=SNAPSHOT_REUSE_MINUTES)
                # Handle naive timestamps from SQLite (assume UTC)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created > cutoff:
                    logger.info(
                        "Reusing recent snapshot #%d for %s (age < %d min)",
                        snap["id"],
                        node_id,
                        SNAPSHOT_REUSE_MINUTES,
                    )
                    # Create new snapshot record with the cached yaml_before
                    return self.db.create_config_snapshot(
                        node_id, push_source, yaml_before=snap["yaml_before"]
                    )

        # Fetch live config from device
        try:
            result = self._admin.get_remote_config(node_id)
            if result.success and result.output:
                return self.db.create_config_snapshot(
                    node_id, push_source, yaml_before=result.output
                )
            else:
                # Snapshot failed — create record so push can still proceed
                snap_id = self.db.create_config_snapshot(node_id, push_source)
                self.db.update_config_snapshot(
                    snap_id,
                    status="snapshot_failed",
                    error=result.error or "Empty config returned",
                )
                logger.warning(
                    "Snapshot failed for %s (push will proceed without safety net): %s",
                    node_id,
                    result.error,
                )
                return snap_id
        except Exception as exc:
            snap_id = self.db.create_config_snapshot(node_id, push_source)
            self.db.update_config_snapshot(snap_id, status="snapshot_failed", error=str(exc))
            logger.warning("Snapshot exception for %s: %s", node_id, exc)
            return snap_id

    def mark_push_completed(self, snapshot_id: int, yaml_after: str) -> None:
        """Mark push as completed, start monitoring window."""
        now = datetime.now(timezone.utc)
        monitoring_until = now + timedelta(minutes=self.monitoring_minutes)
        # Use SQLite-compatible datetime format (no T, no timezone suffix)
        # so monitoring_until compares correctly with datetime('now') in SQL.
        fmt = "%Y-%m-%d %H:%M:%S"
        self.db.update_config_snapshot(
            snapshot_id,
            yaml_after=yaml_after,
            status="monitoring",
            push_completed_at=now.strftime(fmt),
            monitoring_until=monitoring_until.strftime(fmt),
        )
        logger.info(
            "Snapshot #%d: push completed, monitoring until %s",
            snapshot_id,
            monitoring_until.isoformat(),
        )

    def mark_push_failed(self, snapshot_id: int, error: str) -> None:
        """Mark push as failed (no monitoring needed)."""
        self.db.update_config_snapshot(snapshot_id, status="push_failed", error=error)

    # ── Rollback ──────────────────────────────────────────────────────

    def auto_rollback(self, snapshot_id: int) -> dict:
        """Roll back to yaml_before. Creates alerts for the lifecycle."""
        snapshot = self.db.get_config_snapshot(snapshot_id)
        if not snapshot:
            return {"error": f"Snapshot {snapshot_id} not found"}

        node_id = snapshot["node_id"]
        yaml_before = snapshot["yaml_before"]

        if not yaml_before:
            self.db.update_config_snapshot(
                snapshot_id,
                status="rollback_failed",
                error="No yaml_before available for rollback",
            )
            self._create_alert(
                node_id,
                AlertType.CONFIG_ROLLBACK_FAILED,
                f"Rollback failed for snapshot #{snapshot_id}: no pre-push config available",
            )
            return {"success": False, "error": "No yaml_before available"}

        # Create "triggered" alert
        self._create_alert(
            node_id,
            AlertType.CONFIG_ROLLBACK_TRIGGERED,
            f"Auto-rollback triggered for snapshot #{snapshot_id} "
            f"(source: {snapshot['push_source']})",
        )

        # Write yaml_before to temp file and apply
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
                tmp.write(yaml_before)
                tmp_path = tmp.name

            result = self._admin.apply_remote_config(node_id, tmp_path)

            if result.success:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                self.db.update_config_snapshot(
                    snapshot_id, status="rolled_back", rolled_back_at=now
                )
                self._create_alert(
                    node_id,
                    AlertType.CONFIG_ROLLBACK_COMPLETED,
                    f"Rollback completed for snapshot #{snapshot_id}",
                )
                logger.info("Rollback completed for snapshot #%d on %s", snapshot_id, node_id)
                return {"success": True, "node_id": node_id, "snapshot_id": snapshot_id}
            else:
                self.db.update_config_snapshot(
                    snapshot_id,
                    status="rollback_failed",
                    error=result.error,
                )
                self._create_alert(
                    node_id,
                    AlertType.CONFIG_ROLLBACK_FAILED,
                    f"Rollback failed for snapshot #{snapshot_id}: {result.error}",
                )
                return {"success": False, "error": result.error}

        except Exception as exc:
            self.db.update_config_snapshot(snapshot_id, status="rollback_failed", error=str(exc))
            self._create_alert(
                node_id,
                AlertType.CONFIG_ROLLBACK_FAILED,
                f"Rollback exception for snapshot #{snapshot_id}: {exc}",
            )
            logger.exception("Rollback failed for snapshot #%d", snapshot_id)
            return {"success": False, "error": str(exc)}

    def manual_rollback(self, snapshot_id: int) -> dict:
        """Manually trigger rollback to a specific snapshot.

        Same as auto_rollback but can target any status (e.g. a 'confirmed'
        snapshot where the operator later discovers a problem).
        """
        snapshot = self.db.get_config_snapshot(snapshot_id)
        if not snapshot:
            return {"error": f"Snapshot {snapshot_id} not found"}
        if not snapshot["yaml_before"]:
            return {"error": "No yaml_before available for rollback"}
        return self.auto_rollback(snapshot_id)

    # ── Monitoring (called by watchdog) ───────────────────────────────

    def check_post_push_failures(self) -> dict:
        """Check monitoring snapshots for offline nodes.

        Called by MeshWatchdog every 2 minutes.  For each snapshot in
        ``monitoring`` status, evaluates whether the node is healthy
        or needs auto-rollback.

        Returns:
            Summary dict with monitoring_count, confirmed, rolled_back counts.

        .. note::

            The decision logic below (``_should_rollback``) is a
            **user-contribution point**.  See the plan for trade-off
            discussion (aggressive vs consecutive-failure vs grace-period).
        """
        snapshots = self.db.get_monitoring_snapshots()
        confirmed = 0
        rolled_back = 0
        errors = 0

        devices = {d["node_id"]: d for d in self.db.list_devices()}

        for snap in snapshots:
            node_id = snap["node_id"]
            device = devices.get(node_id)

            decision = self._should_rollback(snap, device)

            if decision == "confirm":
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                self.db.update_config_snapshot(snap["id"], status="confirmed", confirmed_at=now)
                confirmed += 1
            elif decision == "rollback":
                result = self.auto_rollback(snap["id"])
                if result.get("success"):
                    rolled_back += 1
                else:
                    errors += 1
            # decision == "wait" → do nothing, check again next cycle

        return {
            "monitoring_count": len(snapshots),
            "confirmed": confirmed,
            "rolled_back": rolled_back,
            "errors": errors,
        }

    def _should_rollback(
        self,
        snapshot: dict,
        device: Optional[dict],
    ) -> str:
        """Decide whether to rollback, confirm, or keep waiting.

        This is the monitoring decision logic — a user-contribution point.

        Args:
            snapshot: The config_snapshot row dict.
            device:   The devices row dict (or None if device disappeared).

        Returns:
            One of: "rollback", "confirm", "wait".

        Trade-offs considered:
            - Config pushes often trigger a radio reboot (30-60s offline).
            - Immediate rollback on first offline check would be too aggressive.
            - We use the monitoring_until window as a grace period: only confirm
              or rollback AFTER the window expires, never during.
            - If the monitoring window hasn't expired yet, always "wait".
            - Once expired: offline → rollback, online → confirm.

        TODO: This is a user-contribution point.  Consider:
            - Consecutive failure counts (require 2+ offline checks before rollback)
            - Hysteresis (rollback only if offline for > X minutes continuously)
            - Different strategies per push_source (bulk_push vs drift_remediation)
        """
        # If monitoring window hasn't expired yet, keep waiting
        if snapshot.get("monitoring_until"):
            until = datetime.fromisoformat(snapshot["monitoring_until"])
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < until:
                return "wait"

        # Monitoring window expired — evaluate node health
        if device is None:
            # Device completely unknown — likely removed, rollback won't help
            return "confirm"

        mesh_status = device.get("mesh_status", "unknown")
        if mesh_status in ("reachable", "online"):
            return "confirm"

        # Node is offline/unreachable after monitoring window — rollback
        if snapshot.get("yaml_before"):
            return "rollback"

        # No yaml_before → can't rollback, just confirm to stop monitoring
        return "confirm"

    # ── Query ─────────────────────────────────────────────────────────

    def get_snapshot(self, snapshot_id: int) -> Optional[dict]:
        """Fetch a single snapshot by ID."""
        return self.db.get_config_snapshot(snapshot_id)

    def get_node_history(self, node_id: str, limit: int = 20) -> list[dict]:
        """Fetch snapshot history for a node."""
        return self.db.get_snapshots_for_node(node_id, limit=limit)

    def get_rollback_status(self) -> dict:
        """Summary of rollback system state for the /status API."""
        monitoring = self.db.get_monitoring_snapshots()
        recent = self.db.get_recent_snapshots(limit=100)

        status_counts: dict[str, int] = {}
        for snap in recent:
            s = snap["status"]
            status_counts[s] = status_counts.get(s, 0) + 1

        return {
            "monitoring_count": len(monitoring),
            "monitoring_nodes": [s["node_id"] for s in monitoring],
            "recent_snapshot_count": len(recent),
            "status_breakdown": status_counts,
            "monitoring_minutes": self.monitoring_minutes,
        }

    # ── Helpers ────────────────────────────────────────────────────────

    def _create_alert(self, node_id: str, alert_type: AlertType, message: str) -> int:
        """Create an alert with severity from the global map."""
        severity = ALERT_SEVERITY_MAP[alert_type].value
        return self.db.create_alert(node_id, alert_type.value, severity, message)
