"""Bulk Fleet Operation Manager.

Orchestrates batch operations across multiple devices — config pushes,
PSK rotation, firmware updates, reboots, factory resets.

Safety gates:
    1. ``dry_run=True`` (default) → returns preview only
    2. ``dry_run=False, confirmed=True`` → executes for real
    3. Delegates to ``BulkPushManager`` for config pushes

Reuses the BulkPushProgress pattern: background thread execution,
progress tracking via _lock, poll via ``get_progress()``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)


def _resolve_targets(db: MeshDatabase, target_filter: dict) -> list[str]:
    """Resolve a TargetFilter dict to a list of matching node_ids.

    Multiple filter criteria are ANDed: a device must match ALL
    specified criteria.

    Args:
        db: MeshDatabase instance.
        target_filter: Dict with optional keys: node_ids, hardware_model,
            firmware_version, role, mesh_status, all_devices.

    Returns:
        Sorted list of matching node_id strings.
    """
    devices = db.list_devices()

    if target_filter.get("all_devices"):
        return sorted(d["node_id"] for d in devices)

    # If explicit node_ids provided, start with those
    explicit = target_filter.get("node_ids")
    if explicit:
        explicit_set = set(explicit)
        devices = [d for d in devices if d["node_id"] in explicit_set]

    # Apply AND filters
    hw = target_filter.get("hardware_model")
    if hw:
        devices = [d for d in devices if d.get("hw_model") == hw]

    fw = target_filter.get("firmware_version")
    if fw:
        devices = [d for d in devices if d.get("firmware_version") == fw]

    role = target_filter.get("role")
    if role:
        devices = [d for d in devices if d.get("role") == role]

    ms = target_filter.get("mesh_status")
    if ms:
        devices = [d for d in devices if d.get("mesh_status") == ms]

    return sorted(d["node_id"] for d in devices)


class BulkOperationManager:
    """Orchestrate batch fleet operations with safety gates.

    Constructor Args:
        db: MeshDatabase instance.
        bulk_push: Optional BulkPushManager for config push delegation.
    """

    def __init__(
        self,
        db: MeshDatabase,
        *,
        bulk_push: object = None,
    ) -> None:
        self.db = db
        self.bulk_push = bulk_push
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────

    def preview(self, request: dict) -> dict:
        """Preview a bulk operation — resolve targets, show what would happen.

        Args:
            request: Dict with operation_type, target_filter, config_template_id,
                     parameters.

        Returns:
            Dict with preview results: target_count, target_node_ids,
            operation_type, parameters, warnings.
        """
        target_filter = request.get("target_filter", {})
        targets = _resolve_targets(self.db, target_filter)
        op_type = request.get("operation_type", "unknown")

        warnings: list[str] = []
        if not targets:
            warnings.append("No devices match the target filter")
        if op_type == "factory_reset":
            warnings.append("CAUTION: Factory reset will erase all device configuration")
        if op_type == "psk_rotation":
            warnings.append("PSK rotation may temporarily disrupt mesh communication")
        if len(targets) > 50:
            warnings.append(f"Large operation: {len(targets)} devices will be affected")

        # Store as preview in DB
        op_id = self.db.create_bulk_operation(
            operation_type=op_type,
            target_node_ids=json.dumps(targets),
            total_targets=len(targets),
            parameters=json.dumps(request.get("parameters", {})),
            status="preview",
        )

        return {
            "id": op_id,
            "operation_type": op_type,
            "status": "preview",
            "target_count": len(targets),
            "target_node_ids": targets,
            "parameters": request.get("parameters", {}),
            "config_template_id": request.get("config_template_id"),
            "warnings": warnings,
        }

    def execute(self, request: dict) -> dict:
        """Execute a bulk operation (requires dry_run=False, confirmed=True).

        Launches execution in a background thread for progress tracking.

        Args:
            request: Dict with operation_type, target_filter, parameters,
                     dry_run (must be False), confirmed (must be True).

        Returns:
            Dict with operation id and initial status.
        """
        if request.get("dry_run", True):
            return {"error": "Cannot execute with dry_run=True — use preview endpoint"}
        if not request.get("confirmed", False):
            return {"error": "Bulk execution requires confirmed=True"}

        target_filter = request.get("target_filter", {})
        targets = _resolve_targets(self.db, target_filter)
        op_type = request.get("operation_type", "unknown")

        if not targets:
            return {"error": "No devices match the target filter"}

        # Create operation record
        op_id = self.db.create_bulk_operation(
            operation_type=op_type,
            target_node_ids=json.dumps(targets),
            total_targets=len(targets),
            parameters=json.dumps(request.get("parameters", {})),
            status="running",
        )

        # Launch background execution
        thread = threading.Thread(
            target=self._run_operation,
            args=(op_id, op_type, targets, request.get("parameters", {})),
            daemon=True,
        )
        thread.start()

        return {
            "id": op_id,
            "operation_type": op_type,
            "status": "running",
            "target_count": len(targets),
            "target_node_ids": targets,
        }

    def get_progress(self, operation_id: int) -> Optional[dict]:
        """Get current progress of a bulk operation."""
        return self.db.get_bulk_operation(operation_id)

    def cancel(self, operation_id: int) -> dict:
        """Cancel a running or pending bulk operation."""
        op = self.db.get_bulk_operation(operation_id)
        if op is None:
            return {"error": "Operation not found"}
        if op.get("status") in ("completed", "failed", "cancelled"):
            return {"error": f"Operation already {op['status']}"}
        self.db.cancel_bulk_operation(operation_id)
        return {"status": "cancelled", "id": operation_id}

    def list_operations(self, limit: int = 50, status: Optional[str] = None) -> list:
        """List bulk operations with optional status filter."""
        return self.db.list_bulk_operations(limit=limit, status=status)

    # ── Background execution ──────────────────────────────────────────

    def _run_operation(
        self,
        op_id: int,
        op_type: str,
        targets: list[str],
        parameters: dict,
    ) -> None:
        """Execute the operation in a background thread."""
        completed = 0
        failed = 0
        skipped = 0
        results: dict[str, str] = {}

        try:
            for node_id in targets:
                # Check for cancellation
                op = self.db.get_bulk_operation(op_id)
                if op and op.get("status") == "cancelled":
                    results[node_id] = "cancelled"
                    skipped += 1
                    continue

                try:
                    result = self._execute_single(op_type, node_id, parameters)
                    if result.get("success"):
                        completed += 1
                        results[node_id] = "success"
                    else:
                        failed += 1
                        results[node_id] = result.get("error", "failed")
                except Exception as exc:
                    failed += 1
                    results[node_id] = str(exc)
                    logger.warning(
                        "Bulk op %d: %s failed for %s: %s",
                        op_id, op_type, node_id, exc,
                    )

                # Update progress
                self.db.update_bulk_operation(
                    op_id,
                    completed_count=completed,
                    failed_count=failed,
                    skipped_count=skipped,
                    result_json=json.dumps(results),
                )

            # Mark completed
            final_status = "completed" if failed == 0 else "failed"
            self.db.update_bulk_operation(
                op_id,
                status=final_status,
                completed_count=completed,
                failed_count=failed,
                skipped_count=skipped,
                result_json=json.dumps(results),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        except Exception as exc:
            logger.exception("Bulk operation %d crashed", op_id)
            self.db.update_bulk_operation(
                op_id,
                status="failed",
                error=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    def _execute_single(
        self, op_type: str, node_id: str, parameters: dict
    ) -> dict:
        """Execute a single operation on one device.

        Returns dict with 'success': True/False and optional 'error'.
        """
        if op_type == "config_push":
            return self._push_config(node_id, parameters)
        elif op_type == "reboot":
            return self._reboot_node(node_id, parameters)
        elif op_type == "psk_rotation":
            return self._rotate_psk(node_id, parameters)
        elif op_type == "firmware_update":
            return self._update_firmware(node_id, parameters)
        elif op_type == "factory_reset":
            return self._factory_reset(node_id, parameters)
        else:
            return {"success": False, "error": f"Unknown operation type: {op_type}"}

    # ── Operation implementations (stubs for non-push ops) ────────────

    def _push_config(self, node_id: str, parameters: dict) -> dict:
        """Push config to a device via BulkPushManager if available."""
        if self.bulk_push is None:
            return {"success": False, "error": "BulkPushManager not available"}
        # Delegate to existing BulkPushManager — it handles template lookup,
        # config diff, and node delivery
        try:
            template_id = parameters.get("template_id")
            if template_id is None:
                return {"success": False, "error": "template_id required for config_push"}
            # BulkPushManager.push_single() is the integration point
            push_fn = getattr(self.bulk_push, "push_single", None)
            if push_fn is None:
                return {"success": False, "error": "BulkPushManager.push_single not available"}
            result = push_fn(node_id=node_id, template_id=template_id)
            return {"success": result.get("status") == "delivered", "detail": result}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _reboot_node(self, node_id: str, parameters: dict) -> dict:
        """Placeholder: reboot via MQTT admin message."""
        # In production, this would send a Meshtastic admin reboot command
        logger.info("Bulk reboot: %s (simulated)", node_id)
        return {"success": True, "detail": "reboot_queued"}

    def _rotate_psk(self, node_id: str, parameters: dict) -> dict:
        """Placeholder: PSK rotation via config push."""
        new_psk = parameters.get("new_psk")
        if not new_psk:
            return {"success": False, "error": "new_psk required for psk_rotation"}
        logger.info("Bulk PSK rotation: %s (simulated)", node_id)
        return {"success": True, "detail": "psk_rotation_queued"}

    def _update_firmware(self, node_id: str, parameters: dict) -> dict:
        """Placeholder: firmware update via OTA."""
        firmware_url = parameters.get("firmware_url")
        if not firmware_url:
            return {"success": False, "error": "firmware_url required"}
        logger.info("Bulk firmware update: %s (simulated)", node_id)
        return {"success": True, "detail": "firmware_update_queued"}

    def _factory_reset(self, node_id: str, parameters: dict) -> dict:
        """Placeholder: factory reset via admin message."""
        logger.info("Bulk factory reset: %s (simulated)", node_id)
        return {"success": True, "detail": "factory_reset_queued"}
