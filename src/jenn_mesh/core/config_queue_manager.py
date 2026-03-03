"""Config queue manager — store-and-forward retry loop for offline radios."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from jenn_mesh.agent.remote_admin import RemoteAdmin
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.config_queue import (
    DEFAULT_MAX_RETRIES,
    RETRY_LOOP_INTERVAL_SECONDS,
    ConfigQueueEntry,
    ConfigQueueStatus,
    compute_next_retry_delay,
)

logger = logging.getLogger(__name__)


class ConfigQueueManager:
    """Manages the store-and-forward config queue for offline radios.

    Lifecycle:
        1. BulkPushManager failure → enqueue() stores failed push in DB
        2. Background retry loop polls for due entries every 30s
        3. For each due entry, attempt RemoteAdmin.apply_remote_config()
        4. Success → mark delivered; failure → increment retry + backoff
        5. Max retries exceeded → mark failed_permanent + create alert
        6. Dashboard API provides manual retry/cancel
    """

    def __init__(
        self,
        db: MeshDatabase,
        configs_dir: Optional[Path] = None,
        admin_port: str = "auto",
    ):
        self._db = db
        if configs_dir is not None:
            self._configs_dir = configs_dir
        else:
            from jenn_mesh.core.config_manager import CONFIGS_DIR

            self._configs_dir = CONFIGS_DIR
        self._admin_port = admin_port

    def enqueue(
        self,
        target_node_id: str,
        template_role: str,
        config_hash: str,
        yaml_content: str,
        source_push_id: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> ConfigQueueEntry:
        """Add a failed config push to the retry queue.

        Returns the created ConfigQueueEntry.
        """
        entry_id = self._db.create_config_queue_entry(
            target_node_id=target_node_id,
            template_role=template_role,
            config_hash=config_hash,
            yaml_content=yaml_content,
            source_push_id=source_push_id,
            max_retries=max_retries,
        )
        row = self._db.get_config_queue_entry(entry_id)
        if row is None:  # pragma: no cover
            raise RuntimeError(f"Failed to read back config queue entry {entry_id}")
        return ConfigQueueEntry(**row)

    def process_pending(self) -> dict:
        """Process all due queue entries. Called by the retry loop.

        Returns dict with counts: attempted, delivered, failed, escalated.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        entries = self._db.get_pending_queue_entries(now_iso)

        result = {"attempted": 0, "delivered": 0, "failed": 0, "escalated": 0}

        for entry in entries:
            result["attempted"] += 1
            success = self._attempt_delivery(entry)
            if success:
                result["delivered"] += 1
            else:
                # Check if we just hit max retries
                refreshed = self._db.get_config_queue_entry(entry["id"])
                if refreshed and refreshed["status"] == ConfigQueueStatus.FAILED_PERMANENT.value:
                    result["escalated"] += 1
                else:
                    result["failed"] += 1

        return result

    def _attempt_delivery(self, entry: dict) -> bool:
        """Try to push config to the target device via RemoteAdmin.

        Returns True on success, False on failure.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Mark as retrying
        self._db.update_config_queue_status(
            entry["id"],
            ConfigQueueStatus.RETRYING.value,
            last_retry_at=now_iso,
        )

        # Write YAML to a temp file for RemoteAdmin
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
                tmp.write(entry["yaml_content"])
                tmp_path = tmp.name

            admin = RemoteAdmin(port=self._admin_port)
            result = admin.apply_remote_config(entry["target_node_id"], tmp_path)
        except Exception as exc:
            result = None
            error_msg = str(exc)
            logger.error(
                "Config delivery to %s failed: %s",
                entry["target_node_id"],
                exc,
            )
        else:
            error_msg = result.error if not result.success else ""
        finally:
            # Clean up temp file
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

        if result is not None and result.success:
            self._db.update_config_queue_status(
                entry["id"],
                ConfigQueueStatus.DELIVERED.value,
                delivered_at=now_iso,
            )
            logger.info(
                "Config delivered to %s (entry %d)",
                entry["target_node_id"],
                entry["id"],
            )
            return True

        # Failure path — increment retry count and compute next retry
        new_retry_count = entry["retry_count"] + 1
        if new_retry_count >= entry["max_retries"]:
            self._escalate(entry, error_msg, now_iso)
        else:
            delay = compute_next_retry_delay(new_retry_count)
            next_retry = now + timedelta(seconds=delay)
            self._db.update_config_queue_status(
                entry["id"],
                ConfigQueueStatus.PENDING.value,
                retry_count=new_retry_count,
                last_error=error_msg,
                next_retry_at=next_retry.isoformat(),
            )
            logger.info(
                "Config delivery to %s failed (attempt %d/%d), " "next retry in %ds: %s",
                entry["target_node_id"],
                new_retry_count,
                entry["max_retries"],
                delay,
                error_msg,
            )
        return False

    def _escalate(self, entry: dict, error_msg: str, now_iso: str) -> None:
        """Mark entry as permanently failed and create a fleet alert."""
        self._db.update_config_queue_status(
            entry["id"],
            ConfigQueueStatus.FAILED_PERMANENT.value,
            retry_count=entry["retry_count"] + 1,
            last_error=error_msg,
            escalated_at=now_iso,
        )
        # Create fleet alert
        from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType

        alert_type = AlertType.CONFIG_PUSH_FAILED
        severity = ALERT_SEVERITY_MAP[alert_type]
        self._db.create_alert(
            node_id=entry["target_node_id"],
            alert_type=alert_type.value,
            severity=severity.value,
            message=(
                f"Config push to {entry['target_node_id']} "
                f"(role={entry['template_role']}) failed after "
                f"{entry['max_retries']} retries: {error_msg}"
            ),
        )
        logger.warning(
            "Config push to %s escalated after %d retries",
            entry["target_node_id"],
            entry["max_retries"],
        )

    def manual_retry(self, entry_id: int) -> Optional[dict]:
        """Reset a failed/cancelled entry to pending for immediate retry.

        Does NOT reset retry_count (preserves audit trail).
        Returns the updated entry, or None if entry not found / not retryable.
        """
        entry = self._db.get_config_queue_entry(entry_id)
        if entry is None:
            return None
        if entry["status"] not in (
            ConfigQueueStatus.FAILED_PERMANENT.value,
            ConfigQueueStatus.CANCELLED.value,
        ):
            return None

        now_iso = datetime.now(timezone.utc).isoformat()
        self._db.update_config_queue_status(
            entry_id,
            ConfigQueueStatus.PENDING.value,
            next_retry_at=now_iso,
        )
        return self._db.get_config_queue_entry(entry_id)

    def cancel_entry(self, entry_id: int) -> bool:
        """Cancel a pending/retrying entry. Returns True on success."""
        return self._db.cancel_config_queue_entry(entry_id)

    def get_entry(self, entry_id: int) -> Optional[dict]:
        """Get a single queue entry by ID."""
        return self._db.get_config_queue_entry(entry_id)

    def list_entries(
        self,
        target_node_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """List queue entries with optional filters."""
        return self._db.list_config_queue(target_node_id=target_node_id, status=status, limit=limit)

    def get_queue_summary(self) -> dict:
        """Get aggregate queue status counts for health/dashboard."""
        return self._db.get_config_queue_stats()

    def get_device_queue_status(self, node_id: str) -> dict:
        """Get queue status for a specific device."""
        entries = self._db.list_config_queue(target_node_id=node_id)
        pending = [
            e
            for e in entries
            if e["status"]
            in (
                ConfigQueueStatus.PENDING.value,
                ConfigQueueStatus.RETRYING.value,
            )
        ]
        return {
            "node_id": node_id,
            "total_entries": len(entries),
            "pending_count": len(pending),
            "entries": entries,
        }


async def retry_loop_task(manager: ConfigQueueManager) -> None:
    """Background task: process pending config queue entries periodically.

    Uses asyncio.to_thread() to call the synchronous process_pending()
    method (which uses RemoteAdmin subprocess calls that block).
    """
    while True:
        try:
            result = await asyncio.to_thread(manager.process_pending)
            if result["attempted"] > 0:
                logger.info(
                    "Config queue retry: attempted=%d delivered=%d " "failed=%d escalated=%d",
                    result["attempted"],
                    result["delivered"],
                    result["failed"],
                    result["escalated"],
                )
        except Exception:
            logger.exception("Config queue retry loop error")
        await asyncio.sleep(RETRY_LOOP_INTERVAL_SECONDS)
