"""Drift remediation — one-click config drift fix via RemoteAdmin push."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from jenn_mesh.agent.remote_admin import RemoteAdmin
from jenn_mesh.core.config_manager import ConfigManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.device import ConfigHash

logger = logging.getLogger(__name__)


class DriftRemediationManager:
    """Coordinates config drift remediation across ConfigManager, RemoteAdmin,
    and (optionally) ConfigQueueManager.

    When drift is detected (device config hash ≠ golden template hash), this
    manager pushes the golden template back to the device via PKC remote admin.
    Failed pushes auto-enqueue into ConfigQueueManager for store-and-forward
    retry with exponential backoff.
    """

    def __init__(
        self,
        db: MeshDatabase,
        configs_dir: Optional[Path] = None,
        admin_port: str = "auto",
        config_queue: "Optional[object]" = None,
        rollback_manager: Optional[object] = None,
    ):
        self._db = db
        self._config_manager = ConfigManager(db, configs_dir)
        self._admin_port = admin_port
        self._config_queue = config_queue  # Optional ConfigQueueManager
        self._rollback_manager = rollback_manager  # Optional ConfigRollbackManager

    def preview_remediation(self, node_id: str) -> dict:
        """Get remediation preview for a drifted device.

        Shows the golden template YAML that will be pushed, along with
        hash comparison data so the operator can see what's drifted.

        Args:
            node_id: Target device.

        Returns:
            Dict with node_id, long_name, template_role, template_yaml,
            device_hash, template_hash, drifted.

        Raises:
            ValueError: Device not found, no template assigned, or
                        template YAML missing.
        """
        device = self._db.get_device(node_id)
        if device is None:
            raise ValueError(f"Device {node_id} not found")

        template_role = device.get("template_role")
        if not template_role:
            raise ValueError(
                f"Device {node_id} has no template_role assigned — "
                "cannot determine which golden config to push"
            )

        template_yaml = self._config_manager.get_template(template_role)
        if template_yaml is None:
            raise ValueError(f"Golden template '{template_role}' not found in configs or database")

        template_hash = self._config_manager.get_template_hash(template_role)
        device_hash = device.get("config_hash", "")
        drifted = bool(device_hash and template_hash and device_hash != template_hash)

        return {
            "node_id": node_id,
            "long_name": device.get("long_name", ""),
            "template_role": template_role,
            "template_yaml": template_yaml,
            "device_hash": device_hash or "",
            "template_hash": template_hash or "",
            "drifted": drifted,
        }

    def remediate_device(self, node_id: str, operator: str = "dashboard") -> dict:
        """Push golden template to a drifted device via RemoteAdmin.

        1. Fetch golden YAML for device's template_role
        2. Write to temp file
        3. RemoteAdmin.apply_remote_config() over mesh
        4. On success: update hashes, resolve alerts, log audit trail
        5. On failure: enqueue in config_queue if available, log failure

        Args:
            node_id: Target device.
            operator: Who initiated the remediation (for audit trail).

        Returns:
            Dict with node_id, status ("delivered"|"queued"|"failed"),
            template_role, and details.

        Raises:
            ValueError: Device not found or no template assigned.
        """
        device = self._db.get_device(node_id)
        if device is None:
            raise ValueError(f"Device {node_id} not found")

        template_role = device.get("template_role")
        if not template_role:
            raise ValueError(f"Device {node_id} has no template_role assigned")

        template_yaml = self._config_manager.get_template(template_role)
        if template_yaml is None:
            raise ValueError(f"Golden template '{template_role}' not found")

        template_hash = ConfigHash.compute(template_yaml)

        # Snapshot before push (if rollback manager available)
        snapshot_id = None
        if self._rollback_manager is not None:
            try:
                snapshot_id = self._rollback_manager.snapshot_before_push(  # type: ignore[union-attr]  # noqa: E501
                    node_id, "drift_remediation"
                )
            except Exception as snap_err:
                logger.warning("Pre-push snapshot failed for %s: %s", node_id, snap_err)

        # Write YAML to temp file for RemoteAdmin
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
                tmp.write(template_yaml)
                tmp_path = tmp.name

            admin = RemoteAdmin(port=self._admin_port)
            result = admin.apply_remote_config(node_id, tmp_path)

            # Update rollback snapshot status
            if snapshot_id and self._rollback_manager:
                try:
                    if result.success:
                        self._rollback_manager.mark_push_completed(  # type: ignore[union-attr]
                            snapshot_id, template_yaml
                        )
                    else:
                        self._rollback_manager.mark_push_failed(  # type: ignore[union-attr]
                            snapshot_id, result.error or "Unknown error"
                        )
                except Exception as rb_err:
                    logger.warning("Rollback status update failed: %s", rb_err)

            if result.success:
                self._handle_success(node_id, template_role, template_hash, operator)
                logger.info(
                    "Drift remediation succeeded for %s (role=%s, operator=%s)",
                    node_id,
                    template_role,
                    operator,
                )
                return {
                    "node_id": node_id,
                    "status": "delivered",
                    "template_role": template_role,
                    "template_hash": template_hash,
                    "message": f"Golden template '{template_role}' pushed successfully",
                }
            else:
                return self._handle_failure(
                    node_id,
                    template_role,
                    template_hash,
                    template_yaml,
                    result.error,
                    operator,
                )
        except Exception as exc:
            error_msg = str(exc)
            logger.exception("Drift remediation error for %s: %s", node_id, error_msg)
            return self._handle_failure(
                node_id,
                template_role,
                template_hash,
                template_yaml,
                error_msg,
                operator,
            )
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    def remediate_all(self, operator: str = "dashboard") -> dict:
        """Remediate all drifted devices.

        Calls get_drift_report() then remediate_device() for each.
        One device failure does not abort the batch.

        Args:
            operator: Who initiated the remediation.

        Returns:
            Dict with total, delivered, queued, failed, and per-device results.
        """
        drifted = self._config_manager.get_drift_report()
        results: list[dict] = []
        delivered = 0
        queued = 0
        failed = 0

        for entry in drifted:
            try:
                result = self.remediate_device(entry["node_id"], operator=operator)
                results.append(result)
                status = result.get("status", "failed")
                if status == "delivered":
                    delivered += 1
                elif status == "queued":
                    queued += 1
                else:
                    failed += 1
            except ValueError as exc:
                results.append(
                    {
                        "node_id": entry["node_id"],
                        "status": "failed",
                        "message": str(exc),
                    }
                )
                failed += 1

        return {
            "total": len(drifted),
            "delivered": delivered,
            "queued": queued,
            "failed": failed,
            "results": results,
        }

    def get_remediation_status(self, node_id: str) -> dict:
        """Get combined remediation status for a device.

        Aggregates drift state, pending queue entries, active alerts,
        and recent remediation log entries.

        Args:
            node_id: Device to query.

        Returns:
            Dict with node_id, drifted, template_role, pending_queue_entries,
            active_alerts, recent_remediation_log.

        Raises:
            ValueError: Device not found.
        """
        device = self._db.get_device(node_id)
        if device is None:
            raise ValueError(f"Device {node_id} not found")

        # Check drift state
        device_hash = device.get("config_hash", "")
        template_hash = device.get("template_hash", "")
        drifted = bool(device_hash and template_hash and device_hash != template_hash)

        # Pending queue entries
        pending_queue_entries = 0
        if self._config_queue is not None:
            try:
                queue_status = self._config_queue.get_device_queue_status(node_id)
                pending_queue_entries = queue_status.get("pending", 0) + queue_status.get(
                    "retrying", 0
                )
            except Exception:
                pass  # Queue unavailable, report 0

        # Active alerts for this device
        active_alerts = []
        try:
            all_alerts = self._db.get_active_alerts(node_id)
            active_alerts = [
                a
                for a in all_alerts
                if a.get("alert_type") in ("config_drift", "config_push_failed")
            ]
        except Exception:
            pass

        # Recent remediation log
        recent_log = self._db.get_provisioning_log_for_node(
            node_id, action_filter="drift_remediation", limit=5
        )

        return {
            "node_id": node_id,
            "drifted": drifted,
            "template_role": device.get("template_role"),
            "pending_queue_entries": pending_queue_entries,
            "active_alerts": active_alerts,
            "recent_remediation_log": recent_log,
        }

    def _handle_success(
        self,
        node_id: str,
        template_role: str,
        template_hash: str,
        operator: str,
    ) -> None:
        """Handle successful remediation — update state, resolve alerts, audit log."""
        # Update device config_hash to match the pushed template
        with self._db.connection() as conn:
            conn.execute(
                """UPDATE devices SET config_hash = ?, template_hash = ?
                   WHERE node_id = ?""",
                (template_hash, template_hash, node_id),
            )

        # Resolve all active CONFIG_DRIFT and CONFIG_PUSH_FAILED alerts
        active_alerts = self._db.get_active_alerts(node_id)
        for alert in active_alerts:
            if alert.get("alert_type") in ("config_drift", "config_push_failed"):
                self._db.resolve_alert(alert["id"])

        # Audit trail
        self._db.log_provisioning(
            node_id=node_id,
            action="drift_remediation",
            role=template_role,
            template_hash=template_hash,
            operator=operator,
            details=f"Successfully pushed golden template '{template_role}' to fix config drift",
        )

    def _handle_failure(
        self,
        node_id: str,
        template_role: str,
        config_hash: str,
        yaml_content: str,
        error: str,
        operator: str,
    ) -> dict:
        """Handle failed remediation — enqueue for retry if possible, log failure.

        Returns result dict with status 'queued' or 'failed'.
        """
        status = "failed"
        message = f"Push failed: {error}"

        # Enqueue for store-and-forward retry if config_queue is wired
        if self._config_queue is not None:
            try:
                self._config_queue.enqueue(
                    target_node_id=node_id,
                    template_role=template_role,
                    config_hash=config_hash,
                    yaml_content=yaml_content,
                    source_push_id=f"remediation-{node_id}",
                )
                status = "queued"
                message = (
                    f"Push failed ({error}), queued for automatic retry " "with exponential backoff"
                )
                logger.info(
                    "Drift remediation for %s failed, enqueued for retry: %s",
                    node_id,
                    error,
                )
            except Exception as enqueue_exc:
                logger.error(
                    "Failed to enqueue remediation for %s: %s",
                    node_id,
                    enqueue_exc,
                )
        else:
            logger.warning(
                "Drift remediation for %s failed (no config queue): %s",
                node_id,
                error,
            )

        # Audit trail
        self._db.log_provisioning(
            node_id=node_id,
            action="drift_remediation",
            role=template_role,
            template_hash=config_hash,
            operator=operator,
            details=f"Remediation {status}: {error}",
        )

        return {
            "node_id": node_id,
            "status": status,
            "template_role": template_role,
            "message": message,
        }
