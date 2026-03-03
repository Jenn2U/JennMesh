"""Bulk push manager — push golden templates to multiple fleet devices."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from jenn_mesh.agent.remote_admin import RemoteAdmin
from jenn_mesh.core.config_manager import ConfigManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.workbench import (
    BulkPushProgress,
    BulkPushRequest,
    DevicePushEntry,
    PushDeviceStatus,
)

logger = logging.getLogger(__name__)

# Auto-cleanup threshold for completed pushes (seconds)
_CLEANUP_AGE_SECS = 3600  # 1 hour


class BulkPushManager:
    """Manage bulk config push operations to fleet devices.

    Pushes happen sequentially over LoRa mesh (remote admin commands are slow).
    Each push operation runs in a background thread. Clients poll for progress.
    """

    def __init__(
        self,
        db: MeshDatabase,
        configs_dir: Optional[Path] = None,
        admin_port: str = "auto",
        config_queue: Optional[object] = None,
    ):
        self._db = db
        if configs_dir is not None:
            self._configs_dir = configs_dir
        else:
            from jenn_mesh.core.config_manager import CONFIGS_DIR

            self._configs_dir = CONFIGS_DIR
        self._admin_port = admin_port
        self._config_queue = config_queue
        self._pushes: dict[str, BulkPushProgress] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._lock = threading.Lock()

    def start_push(self, request: BulkPushRequest) -> BulkPushProgress:
        """Start a bulk push operation.

        Validates the template exists, creates a progress tracker,
        and spawns a background thread for the actual push.

        Args:
            request: Template name, target device IDs, and dry_run flag.

        Returns:
            BulkPushProgress with initial state (all devices QUEUED).

        Raises:
            ValueError: If the template doesn't exist or no devices specified.
        """
        # Validate template exists
        cm = ConfigManager(self._db, self._configs_dir)
        template = cm.get_template(request.template_name)
        if template is None:
            raise ValueError(f"Template '{request.template_name}' not found")

        if not request.device_ids:
            raise ValueError("No target devices specified")

        # Cleanup old pushes
        self._cleanup_stale()

        push_id = str(uuid.uuid4())[:8]
        devices = [DevicePushEntry(node_id=nid) for nid in request.device_ids]
        progress = BulkPushProgress(
            push_id=push_id,
            template_name=request.template_name,
            total=len(devices),
            queued=len(devices),
            devices=devices,
        )

        with self._lock:
            self._pushes[push_id] = progress
            self._cancel_flags[push_id] = False

        if request.dry_run:
            # Dry run: mark all as skipped immediately
            with self._lock:
                for dev in progress.devices:
                    dev.status = PushDeviceStatus.SKIPPED
                progress.skipped = len(devices)
                progress.queued = 0
                progress.is_complete = True
        else:
            # Spawn background thread for actual push
            thread = threading.Thread(
                target=self._execute_push,
                args=(push_id,),
                daemon=True,
                name=f"bulk-push-{push_id}",
            )
            thread.start()

        return progress

    def get_progress(self, push_id: str) -> Optional[BulkPushProgress]:
        """Get current progress of a push operation."""
        with self._lock:
            return self._pushes.get(push_id)

    def cancel_push(self, push_id: str) -> bool:
        """Request cancellation of a running push.

        Returns True if the cancel was accepted, False if push not found.
        Remaining queued devices will be marked SKIPPED.
        """
        with self._lock:
            if push_id not in self._pushes:
                return False
            self._cancel_flags[push_id] = True
            return True

    def list_pushes(self) -> list[BulkPushProgress]:
        """List all tracked push operations (active and recent)."""
        with self._lock:
            return list(self._pushes.values())

    # ── Internal ─────────────────────────────────────────────────────

    def _execute_push(self, push_id: str) -> None:
        """Background thread: push template to each device sequentially."""
        with self._lock:
            progress = self._pushes.get(push_id)
            if progress is None:
                return

        cm = ConfigManager(self._db, self._configs_dir)
        template_path = self._configs_dir / f"{progress.template_name}.yaml"

        # If no YAML file on disk, write it from DB
        if not template_path.exists():
            template_content = cm.get_template(progress.template_name)
            if template_content:
                template_path.parent.mkdir(parents=True, exist_ok=True)
                template_path.write_text(template_content)

        admin = RemoteAdmin(port=self._admin_port)

        # Read YAML content + hash for config queue (if wired)
        _yaml_content = ""
        _config_hash = ""
        if self._config_queue is not None and template_path.exists():
            try:
                _yaml_content = template_path.read_text()
                from jenn_mesh.core.config_manager import ConfigHash

                _config_hash = ConfigHash.compute(_yaml_content)
            except Exception:
                pass  # Non-critical — queue won't store content

        for device in progress.devices:
            # Check for cancellation
            with self._lock:
                if self._cancel_flags.get(push_id, False):
                    # Mark remaining as skipped
                    for d in progress.devices:
                        if d.status == PushDeviceStatus.QUEUED:
                            d.status = PushDeviceStatus.SKIPPED
                            progress.skipped += 1
                            progress.queued -= 1
                    progress.is_complete = True
                    progress.error = "Cancelled by user"
                    return

            # Push to this device
            now_str = datetime.now(timezone.utc).isoformat()
            with self._lock:
                device.status = PushDeviceStatus.PUSHING
                device.started_at = now_str
                progress.pushing += 1
                progress.queued -= 1

            try:
                result = admin.apply_remote_config(device.node_id, str(template_path))
                completed_str = datetime.now(timezone.utc).isoformat()

                with self._lock:
                    device.completed_at = completed_str
                    progress.pushing -= 1
                    if result.success:
                        device.status = PushDeviceStatus.SUCCESS
                        progress.success += 1
                    else:
                        device.status = PushDeviceStatus.FAILED
                        device.error = result.error or "Unknown error"
                        progress.failed += 1

                # Enqueue failed push for retry (if queue wired)
                if not result.success and self._config_queue and _yaml_content:
                    self._enqueue_failed(
                        device.node_id,
                        progress.template_name,
                        _config_hash,
                        _yaml_content,
                        push_id,
                    )

                # Audit trail
                self._db.log_provisioning(
                    node_id=device.node_id,
                    action="bulk_push",
                    role=progress.template_name,
                    details=(
                        f"{'Success' if result.success else 'Failed'}: "
                        f"{result.error or result.output or 'OK'}"
                    ),
                )

            except Exception as e:
                logger.error("Push to %s failed: %s", device.node_id, e)
                with self._lock:
                    device.status = PushDeviceStatus.FAILED
                    device.error = str(e)
                    device.completed_at = datetime.now(timezone.utc).isoformat()
                    progress.pushing -= 1
                    progress.failed += 1

                # Enqueue failed push for retry (if queue wired)
                if self._config_queue and _yaml_content:
                    self._enqueue_failed(
                        device.node_id,
                        progress.template_name,
                        _config_hash,
                        _yaml_content,
                        push_id,
                    )

        with self._lock:
            progress.is_complete = True

    def _enqueue_failed(
        self,
        node_id: str,
        template_name: str,
        config_hash: str,
        yaml_content: str,
        push_id: str,
    ) -> None:
        """Enqueue a failed push into the config queue for retry."""
        try:
            self._config_queue.enqueue(  # type: ignore[union-attr]
                target_node_id=node_id,
                template_role=template_name,
                config_hash=config_hash,
                yaml_content=yaml_content,
                source_push_id=push_id,
            )
            logger.info(
                "Queued failed push for %s in config queue (push_id=%s)",
                node_id,
                push_id,
            )
        except Exception as enq_err:
            logger.error("Failed to enqueue config for %s: %s", node_id, enq_err)

    def _cleanup_stale(self) -> None:
        """Remove completed pushes older than the cleanup threshold."""
        now = datetime.now(timezone.utc)
        stale_ids: list[str] = []

        with self._lock:
            for push_id, progress in self._pushes.items():
                if not progress.is_complete:
                    continue
                # Check last device's completed_at for age
                last_completed = None
                for d in progress.devices:
                    if d.completed_at:
                        last_completed = d.completed_at
                if last_completed:
                    try:
                        completed_dt = datetime.fromisoformat(last_completed)
                        age = (now - completed_dt).total_seconds()
                        if age > _CLEANUP_AGE_SECS:
                            stale_ids.append(push_id)
                    except (ValueError, TypeError):
                        pass

            for push_id in stale_ids:
                del self._pushes[push_id]
                self._cancel_flags.pop(push_id, None)
