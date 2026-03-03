"""Automated failover — assess, execute, revert compensations when relay SPOFs fail."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from jenn_mesh.agent.remote_admin import RemoteAdmin
from jenn_mesh.core.topology import TopologyManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType

logger = logging.getLogger(__name__)


class FailoverManager:
    """Coordinates automated failover when relay SPOFs go offline.

    When a relay node fails, this manager:
    1. Assesses impact (dependent nodes, compensation candidates)
    2. Generates compensations (TX power, role, hop limit changes)
    3. Applies compensations via RemoteAdmin.set_remote_config()
    4. Tracks lifecycle: active → reverted (when failed node recovers)

    Each compensation stores original_value so it can be cleanly reverted.
    """

    def __init__(
        self,
        db: MeshDatabase,
        topology_manager: Optional[TopologyManager] = None,
        admin_port: str = "auto",
    ):
        self._db = db
        self._topology = topology_manager or TopologyManager(db)
        self._admin_port = admin_port

    def assess_failover_impact(self, node_id: str) -> dict:
        """Assess what would happen if *node_id* fails.

        Returns an impact assessment with dependent nodes, compensation
        candidates, and suggested compensations. Does NOT modify any state.

        Args:
            node_id: Node to assess.

        Returns:
            Dict with failed_node_id, is_spof, dependent_nodes,
            compensation_candidates, suggested_compensations.

        Raises:
            ValueError: Device not found.
        """
        device = self._db.get_device(node_id)
        if device is None:
            raise ValueError(f"Device {node_id} not found")

        spofs = self._topology.find_single_points_of_failure()
        is_spof = node_id in spofs
        dependent_nodes = self._topology.find_dependent_nodes(node_id)
        candidates = self._topology.get_compensation_candidates(node_id)

        assessment = {
            "failed_node_id": node_id,
            "is_spof": is_spof,
            "dependent_nodes": dependent_nodes,
            "compensation_candidates": candidates,
            "suggested_compensations": [],
        }

        # Generate suggested compensations
        if dependent_nodes and candidates:
            assessment["suggested_compensations"] = self._generate_compensations(assessment)

        return assessment

    def execute_failover(self, node_id: str, operator: str = "dashboard") -> dict:
        """Execute failover for a failed node.

        1. Assess impact
        2. Create failover_event record
        3. Generate + apply compensations via RemoteAdmin
        4. Track results (applied/failed per compensation)
        5. Create FAILOVER_ACTIVATED alert + audit log

        Args:
            node_id: Failed node.
            operator: Who initiated (for audit trail).

        Returns:
            Dict with event_id, status, compensations applied/failed.

        Raises:
            ValueError: Device not found or failover already active.
        """
        device = self._db.get_device(node_id)
        if device is None:
            raise ValueError(f"Device {node_id} not found")

        # Check for existing active failover
        existing = self._db.get_active_failover_for_node(node_id)
        if existing is not None:
            raise ValueError(
                f"Active failover already exists for {node_id} (event #{existing['id']})"
            )

        # Assess impact
        assessment = self.assess_failover_impact(node_id)
        dependent_nodes = assessment["dependent_nodes"]

        # Create failover event
        event_id = self._db.create_failover_event(
            failed_node_id=node_id,
            dependent_nodes=json.dumps(dependent_nodes),
            operator=operator,
        )

        # Generate compensations
        compensations = self._generate_compensations(assessment)
        applied_count = 0
        failed_count = 0
        compensation_results: list[dict] = []

        admin = RemoteAdmin(port=self._admin_port)

        for comp in compensations:
            comp_id = self._db.create_failover_compensation(
                event_id=event_id,
                comp_node_id=comp["comp_node_id"],
                comp_type=comp["comp_type"],
                config_key=comp["config_key"],
                original_value=comp["original_value"],
                new_value=comp["new_value"],
            )

            # Apply via RemoteAdmin
            try:
                result = admin.set_remote_config(
                    dest_node_id=comp["comp_node_id"],
                    key=comp["config_key"],
                    value=comp["new_value"],
                )
                if result.success:
                    self._db.update_compensation_status(comp_id, "applied")
                    applied_count += 1
                    compensation_results.append(
                        {"comp_id": comp_id, "node_id": comp["comp_node_id"], "status": "applied"}
                    )
                else:
                    self._db.update_compensation_status(comp_id, "pending", error=result.error)
                    failed_count += 1
                    compensation_results.append(
                        {
                            "comp_id": comp_id,
                            "node_id": comp["comp_node_id"],
                            "status": "failed",
                            "error": result.error,
                        }
                    )
            except Exception as exc:
                error_msg = str(exc)
                self._db.update_compensation_status(comp_id, "pending", error=error_msg)
                failed_count += 1
                compensation_results.append(
                    {
                        "comp_id": comp_id,
                        "node_id": comp["comp_node_id"],
                        "status": "failed",
                        "error": error_msg,
                    }
                )

        # Create alert
        severity = ALERT_SEVERITY_MAP[AlertType.FAILOVER_ACTIVATED]
        if not self._db.has_active_alert(node_id, AlertType.FAILOVER_ACTIVATED.value):
            self._db.create_alert(
                node_id=node_id,
                alert_type=AlertType.FAILOVER_ACTIVATED.value,
                severity=severity.value,
                message=(
                    f"Failover activated: {applied_count} compensations applied, "
                    f"{failed_count} failed, {len(dependent_nodes)} dependent nodes"
                ),
            )

        # Audit trail
        self._db.log_provisioning(
            node_id=node_id,
            action="failover_execute",
            operator=operator,
            details=(
                f"Failover event #{event_id}: {applied_count} applied, "
                f"{failed_count} failed, dependent={dependent_nodes}"
            ),
        )

        logger.info(
            "Failover executed for %s: event=%d, applied=%d, failed=%d",
            node_id,
            event_id,
            applied_count,
            failed_count,
        )

        return {
            "event_id": event_id,
            "failed_node_id": node_id,
            "status": "active",
            "dependent_nodes": dependent_nodes,
            "total_compensations": len(compensations),
            "applied": applied_count,
            "failed": failed_count,
            "compensations": compensation_results,
        }

    def revert_failover(self, event_id: int, operator: str = "dashboard") -> dict:
        """Revert all compensations for a failover event.

        For each applied compensation, pushes the original_value back via
        RemoteAdmin. Updates event and compensation statuses.

        Args:
            event_id: Failover event to revert.
            operator: Who initiated (for audit trail).

        Returns:
            Dict with event status, revert results.

        Raises:
            ValueError: Event not found or not active.
        """
        event = self._db.get_failover_event(event_id)
        if event is None:
            raise ValueError(f"Failover event #{event_id} not found")
        if event["status"] != "active":
            raise ValueError(f"Failover event #{event_id} is '{event['status']}', not 'active'")

        compensations = self._db.get_compensations_for_event(event_id)
        admin = RemoteAdmin(port=self._admin_port)

        reverted_count = 0
        revert_failed_count = 0
        revert_results: list[dict] = []

        for comp in compensations:
            if comp["status"] != "applied":
                continue  # Only revert actually applied compensations

            try:
                result = admin.set_remote_config(
                    dest_node_id=comp["comp_node_id"],
                    key=comp["config_key"],
                    value=comp["original_value"],
                )
                if result.success:
                    self._db.update_compensation_status(comp["id"], "reverted")
                    reverted_count += 1
                    revert_results.append(
                        {
                            "comp_id": comp["id"],
                            "node_id": comp["comp_node_id"],
                            "status": "reverted",
                        }
                    )
                else:
                    self._db.update_compensation_status(
                        comp["id"], "revert_failed", error=result.error
                    )
                    revert_failed_count += 1
                    revert_results.append(
                        {
                            "comp_id": comp["id"],
                            "node_id": comp["comp_node_id"],
                            "status": "revert_failed",
                            "error": result.error,
                        }
                    )
            except Exception as exc:
                error_msg = str(exc)
                self._db.update_compensation_status(comp["id"], "revert_failed", error=error_msg)
                revert_failed_count += 1
                revert_results.append(
                    {
                        "comp_id": comp["id"],
                        "node_id": comp["comp_node_id"],
                        "status": "revert_failed",
                        "error": error_msg,
                    }
                )

        # Update event status
        now_iso = datetime.now(timezone.utc).isoformat()
        failed_node = event["failed_node_id"]
        if revert_failed_count > 0:
            self._db.update_failover_event_status(event_id, "revert_failed")
            alert_type = AlertType.FAILOVER_REVERT_FAILED
            severity = ALERT_SEVERITY_MAP[alert_type]
            self._db.create_alert(
                node_id=failed_node,
                alert_type=alert_type.value,
                severity=severity.value,
                message=(
                    f"Failover revert partially failed: {reverted_count} reverted, "
                    f"{revert_failed_count} failed"
                ),
            )
        else:
            self._db.update_failover_event_status(event_id, "reverted", reverted_at=now_iso)
            # Resolve FAILOVER_ACTIVATED alert
            active_alerts = self._db.get_active_alerts(failed_node)
            for alert in active_alerts:
                if alert.get("alert_type") == AlertType.FAILOVER_ACTIVATED.value:
                    self._db.resolve_alert(alert["id"])

            alert_type = AlertType.FAILOVER_REVERTED
            severity = ALERT_SEVERITY_MAP[alert_type]
            self._db.create_alert(
                node_id=failed_node,
                alert_type=alert_type.value,
                severity=severity.value,
                message=f"Failover reverted: {reverted_count} compensations restored",
            )

        # Audit trail
        self._db.log_provisioning(
            node_id=failed_node,
            action="failover_revert",
            operator=operator,
            details=(
                f"Failover event #{event_id} revert: {reverted_count} reverted, "
                f"{revert_failed_count} failed"
            ),
        )

        logger.info(
            "Failover revert for event #%d: reverted=%d, failed=%d",
            event_id,
            reverted_count,
            revert_failed_count,
        )

        return {
            "event_id": event_id,
            "status": "revert_failed" if revert_failed_count > 0 else "reverted",
            "reverted": reverted_count,
            "revert_failed": revert_failed_count,
            "results": revert_results,
        }

    def cancel_failover(self, event_id: int, operator: str = "dashboard") -> dict:
        """Cancel a failover without reverting compensations.

        Marks the event as cancelled. Applied compensations stay in place.

        Args:
            event_id: Failover event to cancel.
            operator: Who cancelled.

        Returns:
            Dict with event status.

        Raises:
            ValueError: Event not found or not active.
        """
        event = self._db.get_failover_event(event_id)
        if event is None:
            raise ValueError(f"Failover event #{event_id} not found")
        if event["status"] != "active":
            raise ValueError(f"Failover event #{event_id} is '{event['status']}', not 'active'")

        now_iso = datetime.now(timezone.utc).isoformat()
        self._db.update_failover_event_status(event_id, "cancelled", cancelled_at=now_iso)

        self._db.log_provisioning(
            node_id=event["failed_node_id"],
            action="failover_cancel",
            operator=operator,
            details=f"Failover event #{event_id} cancelled (compensations not reverted)",
        )

        logger.info("Failover event #%d cancelled by %s", event_id, operator)
        return {"event_id": event_id, "status": "cancelled"}

    def get_failover_status(self, node_id: str) -> dict:
        """Get failover status for a specific node.

        Aggregates: active events, compensation details, active alerts,
        and recent failover provisioning log.

        Args:
            node_id: Device to query.

        Returns:
            Dict with active_event, compensations, active_alerts,
            recent_failover_log.

        Raises:
            ValueError: Device not found.
        """
        device = self._db.get_device(node_id)
        if device is None:
            raise ValueError(f"Device {node_id} not found")

        active_event = self._db.get_active_failover_for_node(node_id)
        compensations: list[dict] = []
        if active_event is not None:
            compensations = self._db.get_compensations_for_event(active_event["id"])

        active_alerts = [
            a
            for a in self._db.get_active_alerts(node_id)
            if a.get("alert_type")
            in (
                AlertType.FAILOVER_ACTIVATED.value,
                AlertType.FAILOVER_REVERTED.value,
                AlertType.FAILOVER_REVERT_FAILED.value,
            )
        ]

        # Get recent failover-related provisioning log entries
        all_recent = self._db.get_provisioning_log_for_node(node_id, limit=20)
        recent_log = [
            entry for entry in all_recent if entry.get("action", "").startswith("failover_")
        ][:10]

        return {
            "node_id": node_id,
            "has_active_failover": active_event is not None,
            "active_event": active_event,
            "compensations": compensations,
            "active_alerts": active_alerts,
            "recent_failover_log": recent_log,
        }

    def list_active_failovers(self) -> list[dict]:
        """List all active failover events with their compensations."""
        events = self._db.list_active_failover_events()
        enriched: list[dict] = []
        for event in events:
            comps = self._db.get_compensations_for_event(event["id"])
            enriched.append({**event, "compensations": comps})
        return enriched

    def check_recoveries(self) -> dict:
        """Check if any failed nodes have come back online and auto-revert.

        For each active failover where the failed node is now online,
        automatically reverts all compensations.

        Returns:
            Dict with checked, recovered, reverted, failed, results.
        """
        active_events = self._db.list_active_failover_events()
        checked = len(active_events)
        recovered = 0
        reverted = 0
        failed = 0
        results: list[dict] = []

        for event in active_events:
            node_id = event["failed_node_id"]
            device = self._db.get_device(node_id)
            if device is None:
                continue

            # Check if node is back online (mesh_status != 'offline')
            mesh_status = device.get("mesh_status", "unknown")
            if mesh_status == "offline":
                continue

            # Node is back! Auto-revert.
            recovered += 1
            try:
                revert_result = self.revert_failover(event["id"], operator="auto_recovery")
                if revert_result["status"] == "reverted":
                    reverted += 1
                else:
                    failed += 1
                results.append(
                    {
                        "event_id": event["id"],
                        "node_id": node_id,
                        "revert_status": revert_result["status"],
                    }
                )
            except Exception as exc:
                failed += 1
                results.append(
                    {
                        "event_id": event["id"],
                        "node_id": node_id,
                        "revert_status": "error",
                        "error": str(exc),
                    }
                )

        logger.info(
            "Recovery check: %d active failovers, %d recovered, %d reverted, %d failed",
            checked,
            recovered,
            reverted,
            failed,
        )

        return {
            "checked": checked,
            "recovered": recovered,
            "reverted": reverted,
            "failed": failed,
            "results": results,
        }

    def _generate_compensations(self, assessment: dict) -> list[dict]:
        """Generate compensation actions from an impact assessment.

        ★ USER CONTRIBUTION — This is the compensation generation strategy.

        Given an impact assessment (failed node, dependent nodes, candidate
        compensators with their current config), decide what compensations to
        apply.

        Trade-offs to consider:
        - Priority: hop_limit_increase (cheapest) → tx_power_increase
          (moderate battery cost) → role_change to ROUTER_CLIENT (heaviest)
        - Don't boost nodes with battery < 30%
        - Meshtastic config keys: lora.tx_power (dBm), lora.hop_limit, device.role
        - TX power max: 30 dBm, hop limit max: 7
        - Don't over-compensate: skip if already at max value

        Args:
            assessment: Output from assess_failover_impact().

        Returns:
            List of dicts, each with: comp_node_id, comp_type, config_key,
            original_value, new_value.
        """
        # TODO: User contribution — implement compensation generation strategy.
        # The scaffolding below applies hop_limit + tx_power boosts to all
        # candidates. Replace or refine this with your preferred strategy.
        compensations: list[dict] = []
        candidates = assessment.get("compensation_candidates", [])

        for candidate in candidates:
            node_id = candidate["node_id"]
            battery = candidate.get("battery_level")

            # Battery guard
            if battery is not None and battery < 30:
                continue

            # 1. Hop limit increase (cheapest compensation)
            compensations.append(
                {
                    "comp_node_id": node_id,
                    "comp_type": "hop_limit_increase",
                    "config_key": "lora.hop_limit",
                    "original_value": "3",
                    "new_value": "7",
                }
            )

            # 2. TX power increase (moderate cost)
            compensations.append(
                {
                    "comp_node_id": node_id,
                    "comp_type": "tx_power_increase",
                    "config_key": "lora.tx_power",
                    "original_value": "17",
                    "new_value": "30",
                }
            )

        return compensations
