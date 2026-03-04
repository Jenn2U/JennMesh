"""Health check endpoint for the JennMesh dashboard.

Returns comprehensive component health — DB, workbench, uptime, schema version.
Pattern follows JennSentry ``page_routes.py`` health endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from jenn_mesh import __version__
from jenn_mesh.db import SCHEMA_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    """Comprehensive dashboard health check."""
    components: dict = {}
    overall = "healthy"

    # 1. Database
    db = getattr(request.app.state, "db", None)
    if db is not None:
        try:
            with db.connection() as conn:
                conn.execute("SELECT 1")
            components["database"] = {"status": "healthy", "schema_version": SCHEMA_VERSION}
        except Exception as exc:
            components["database"] = {"status": "degraded", "error": str(exc)}
            overall = "degraded"
            logger.warning("Health check: database degraded — %s", exc)
    else:
        components["database"] = {"status": "unavailable"}
        overall = "degraded"

    # 2. Workbench manager
    has_workbench = getattr(request.app.state, "workbench", None) is not None
    components["workbench"] = {"status": "healthy" if has_workbench else "unavailable"}

    # 3. Bulk push manager
    has_bulk_push = getattr(request.app.state, "bulk_push", None) is not None
    components["bulk_push"] = {"status": "healthy" if has_bulk_push else "unavailable"}

    # 4. Mesh heartbeats
    if db is not None:
        try:
            recent_hb = db.get_recent_heartbeats(minutes=10)
            components["mesh_heartbeats"] = {
                "status": "healthy",
                "recent_count": len(recent_hb),
            }
        except Exception as exc:
            components["mesh_heartbeats"] = {"status": "degraded", "error": str(exc)}
    else:
        components["mesh_heartbeats"] = {"status": "unavailable"}

    # 5. Emergency broadcasts
    if db is not None:
        try:
            recent_eb = db.get_recent_broadcasts(minutes=60)
            last_broadcast = recent_eb[0]["created_at"] if recent_eb else None
            components["emergency_broadcasts"] = {
                "status": "healthy",
                "recent_count": len(recent_eb),
                "last_broadcast_time": last_broadcast,
            }
        except Exception as exc:
            components["emergency_broadcasts"] = {"status": "degraded", "error": str(exc)}
    else:
        components["emergency_broadcasts"] = {"status": "unavailable"}

    # 6. Recovery commands
    if db is not None:
        try:
            recent_rc = db.get_recent_recovery_commands(minutes=60)
            pending_rc = [c for c in recent_rc if c["status"] in {"pending", "sending", "sent"}]
            last_cmd = recent_rc[0]["created_at"] if recent_rc else None
            components["recovery_commands"] = {
                "status": "healthy",
                "recent_count": len(recent_rc),
                "pending_count": len(pending_rc),
                "last_command_time": last_cmd,
            }
        except Exception as exc:
            components["recovery_commands"] = {"status": "degraded", "error": str(exc)}
    else:
        components["recovery_commands"] = {"status": "unavailable"}

    # 7. Config queue
    cq_manager = getattr(request.app.state, "config_queue_manager", None)
    if cq_manager is not None:
        try:
            summary = cq_manager.get_queue_summary()
            pending = summary.get("pending", 0) + summary.get("retrying", 0)
            failed_perm = summary.get("failed_permanent", 0)
            components["config_queue"] = {
                "status": "healthy",
                "pending_count": pending,
                "failed_permanent_count": failed_perm,
                "total_delivered": summary.get("delivered", 0),
            }
        except Exception as exc:
            components["config_queue"] = {"status": "degraded", "error": str(exc)}
    else:
        components["config_queue"] = {"status": "unavailable"}

    # 8. Drift remediation
    drm = getattr(request.app.state, "drift_remediation_manager", None)
    if drm is not None:
        try:
            cm = __import__("jenn_mesh.core.config_manager", fromlist=["ConfigManager"])
            config_mgr = cm.ConfigManager(db)
            drifted = config_mgr.get_drift_report()
            components["drift_remediation"] = {
                "status": "healthy",
                "drifted_device_count": len(drifted),
            }
        except Exception as exc:
            components["drift_remediation"] = {"status": "degraded", "error": str(exc)}
    else:
        components["drift_remediation"] = {"status": "unavailable"}

    # 9. Failover
    fm = getattr(request.app.state, "failover_manager", None)
    if fm is not None and db is not None:
        try:
            active_events = fm.list_active_failovers()
            components["failover"] = {
                "status": "healthy",
                "active_failover_count": len(active_events),
            }
        except Exception as exc:
            components["failover"] = {"status": "degraded", "error": str(exc)}
    else:
        components["failover"] = {"status": "unavailable"}

    # 10. Mesh watchdog
    wd = getattr(request.app.state, "mesh_watchdog", None)
    if wd is not None:
        try:
            wd_status = wd.get_status()
            components["mesh_watchdog"] = {
                "status": "healthy",
                "total_cycles": wd_status["total_cycles"],
                "enabled_checks": sum(1 for c in wd_status["checks"].values() if c["enabled"]),
            }
        except Exception as exc:
            components["mesh_watchdog"] = {"status": "degraded", "error": str(exc)}
    else:
        components["mesh_watchdog"] = {"status": "unavailable"}

    # 11. Config rollback
    rb = getattr(request.app.state, "config_rollback_manager", None)
    if rb is not None:
        try:
            rb_status = rb.get_rollback_status()
            components["config_rollback"] = {
                "status": "healthy",
                "monitoring_count": rb_status["monitoring_count"],
                "recent_snapshot_count": rb_status["recent_snapshot_count"],
            }
        except Exception as exc:
            components["config_rollback"] = {"status": "degraded", "error": str(exc)}
    else:
        components["config_rollback"] = {"status": "unavailable"}

    # 12. Sync relay
    srm = getattr(request.app.state, "sync_relay_manager", None)
    if srm is not None:
        try:
            sr_status = srm.get_sync_status()
            components["sync_relay"] = {
                "status": "healthy",
                "active_sessions": sr_status.get("active_sessions", 0),
                "queue_depth": sr_status.get("pending_queue_entries", 0),
            }
        except Exception as exc:
            components["sync_relay"] = {"status": "degraded", "error": str(exc)}
    else:
        components["sync_relay"] = {"status": "unavailable"}

    # 13. Encryption audit
    enc_auditor = getattr(request.app.state, "encryption_auditor", None)
    if enc_auditor is not None:
        try:
            score = enc_auditor.get_fleet_encryption_score()
            components["encryption_audit"] = {
                "status": "healthy",
                "fleet_score": score,
            }
        except Exception as exc:
            components["encryption_audit"] = {"status": "degraded", "error": str(exc)}
    else:
        components["encryption_audit"] = {"status": "unavailable"}

    # 14. Webhooks
    wh_mgr = getattr(request.app.state, "webhook_manager", None)
    if wh_mgr is not None:
        try:
            active_hooks = wh_mgr.list_webhooks(active_only=True)
            components["webhooks"] = {
                "status": "healthy",
                "active_webhook_count": len(active_hooks),
            }
        except Exception as exc:
            components["webhooks"] = {"status": "degraded", "error": str(exc)}
    else:
        components["webhooks"] = {"status": "unavailable"}

    # 15. Notifications
    nd = getattr(request.app.state, "notification_dispatcher", None)
    if nd is not None:
        try:
            channels = db.list_notification_channels(active_only=True) if db else []
            rules = db.list_notification_rules(active_only=True) if db else []
            components["notifications"] = {
                "status": "healthy",
                "active_channel_count": len(channels),
                "active_rule_count": len(rules),
            }
        except Exception as exc:
            components["notifications"] = {"status": "degraded", "error": str(exc)}
    else:
        components["notifications"] = {"status": "unavailable"}

    # 16. Partition detection
    pd = getattr(request.app.state, "partition_detector", None)
    if pd is not None:
        try:
            status = pd.get_partition_status()
            components["partition_detection"] = {
                "status": "healthy",
                "is_partitioned": status.get("is_partitioned", False),
                "component_count": status.get("component_count", 1),
            }
        except Exception as exc:
            components["partition_detection"] = {"status": "degraded", "error": str(exc)}
    else:
        components["partition_detection"] = {"status": "unavailable"}

    # 17. Bulk operations
    bom = getattr(request.app.state, "bulk_operation_manager", None)
    if bom is not None:
        try:
            running_ops = bom.list_operations(limit=10, status="running")
            components["bulk_operations"] = {
                "status": "healthy",
                "running_operation_count": len(running_ops),
            }
        except Exception as exc:
            components["bulk_operations"] = {"status": "degraded", "error": str(exc)}
    else:
        components["bulk_operations"] = {"status": "unavailable"}

    # 18. Uptime
    startup_time = getattr(request.app.state, "startup_time", None)
    if startup_time is not None:
        uptime = (datetime.now(timezone.utc) - startup_time).total_seconds()
        components["uptime_seconds"] = round(uptime, 1)

    return {
        "status": overall,
        "version": __version__,
        "service": "jenn-mesh",
        "schema_version": SCHEMA_VERSION,
        "components": components,
    }
