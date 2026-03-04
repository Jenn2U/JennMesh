"""Sync relay manager — gateway-side CRDT sync relay between Production API and LoRa mesh.

Runs on gateway nodes that have BOTH internet (HTTP to Jenn Production) and radio
(LoRa via RadioBridge). When an edge node's heartbeat reveals a stale state vector,
the gateway fetches the delta from Production, fragments it, and sends it over LoRa.
Edge pushes are reassembled and relayed to Production.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from jenn_mesh.core.sync_fragmenter import SyncFragmenter, SyncReassembler
from jenn_mesh.models.fleet import AlertSeverity, AlertType
from jenn_mesh.models.sync_relay import (
    MAX_RETRANSMITS,
    SYNC_ACK_PREFIX,
    SYNC_CHANNEL_INDEX,
    SYNC_FRAG_PREFIX,
    SYNC_NACK_PREFIX,
    SYNC_SV_PREFIX,
    SyncDirection,
    SyncPriority,
    compute_sv_hash,
    format_sync_frag,
    generate_session_id,
    parse_sync_ack,
    parse_sync_frag,
    parse_sync_nack,
    parse_sync_sv,
)

logger = logging.getLogger(__name__)

# Default monitoring window for sync sessions (minutes)
DEFAULT_SYNC_COOLDOWN_MINUTES = 10


class SyncRelayManager:
    """Gateway-side orchestrator: relay CRDT sync between Production HTTP API and LoRa mesh.

    Lifecycle:
        1. Edge heartbeat includes SV hash → gateway detects mismatch
        2. Edge sends SYNC_SV with full state vector
        3. Gateway calls POST /api/v1/sync on Production → gets delta
        4. Gateway strips content for LoRa (metadata only), fragments, and sends
        5. Edge reassembles, merges locally
        6. Edge sends local changes back via SYNC_FRAG → gateway → POST /api/v1/sync/push
    """

    def __init__(
        self,
        db: Any,
        bridge: Optional[Any] = None,
        *,
        production_url: str = "",
        sync_token: str = "",
        cooldown_minutes: int = DEFAULT_SYNC_COOLDOWN_MINUTES,
        http_client: Optional[Any] = None,
    ):
        """Initialize the sync relay manager.

        Args:
            db: MeshDatabase instance for persistence.
            bridge: RadioBridge instance with send_text() method. None if gateway-only.
            production_url: Jenn Production base URL (e.g., "https://jenn2u.ai").
            sync_token: Device token for authenticating with Production sync API.
            cooldown_minutes: Minutes to suppress re-triggering sync after completion.
            http_client: Optional httpx.Client for Production API calls (injectable for tests).
        """
        self.db = db
        self._bridge = bridge
        self._production_url = production_url.rstrip("/")
        self._sync_token = sync_token
        self.cooldown_minutes = cooldown_minutes
        self._http_client = http_client
        self._fragmenter = SyncFragmenter()
        self._reassembler = SyncReassembler()

        # In-memory state tracking
        self._last_sync_by_node: dict[str, float] = {}  # node_id → monotonic timestamp
        self._known_sv_hashes: dict[str, str] = {}  # node_id → last seen SV hash
        self._active_sessions: dict[str, dict] = {}  # session_id → session metadata

    # ── Heartbeat SV hash detection ──────────────────────────────────

    def handle_heartbeat_sv_hash(self, node_id: str, sv_hash: str) -> bool:
        """Called when a heartbeat with SV hash is received from an edge node.

        This is the **user contribution point** — the decision logic for when
        to trigger a full sync exchange based on SV hash comparison.

        Trade-offs to consider:
        - Eager: Trigger on every mismatch → fast convergence, high LoRa cost
        - Debounced: Wait for N consecutive mismatches → tolerates transient clock drift
        - Cooldown: Suppress re-triggering for M minutes after sync completes
        - Queue limit: Only sync one node at a time to avoid channel collisions

        Args:
            node_id: Edge node's Meshtastic node ID.
            sv_hash: 8-char hex hash of the edge node's current state vector.

        Returns:
            True if a sync was triggered, False if suppressed or no change.
        """
        # TODO(#579): Implement sync trigger decision logic — user contribution
        # For now, use cooldown-based approach: trigger if hash differs AND
        # cooldown has elapsed since last sync for this node.
        old_hash = self._known_sv_hashes.get(node_id)
        self._known_sv_hashes[node_id] = sv_hash

        if old_hash == sv_hash:
            return False  # No change

        # Check cooldown
        last_sync = self._last_sync_by_node.get(node_id, 0)
        elapsed = time.monotonic() - last_sync
        if elapsed < self.cooldown_minutes * 60:
            logger.debug(
                "SyncRelay: SV mismatch for %s but cooldown active (%.0fs remaining)",
                node_id,
                self.cooldown_minutes * 60 - elapsed,
            )
            return False

        # Check if another session is already active for this node
        for sess in self._active_sessions.values():
            if sess.get("node_id") == node_id and sess.get("status") == "sending":
                logger.debug("SyncRelay: skipping %s — session already active", node_id)
                return False

        logger.info(
            "SyncRelay: SV hash mismatch for %s (old=%s new=%s) — requesting full SV",
            node_id,
            old_hash,
            sv_hash,
        )

        # Create alert for SV mismatch detection
        self._create_alert(
            node_id,
            AlertType.SYNC_SV_MISMATCH,
            f"State vector mismatch detected for {node_id} (hash: {sv_hash})",
        )
        return True

    # ── Incoming mesh text routing ───────────────────────────────────

    def handle_mesh_text(self, text: str, from_id: str = "") -> bool:
        """Route incoming SYNC_* messages from mesh. Returns True if consumed.

        Called by the MQTT subscriber or agent text callback for all incoming text.
        """
        if not text.startswith("SYNC_"):
            return False

        if text.startswith(SYNC_SV_PREFIX):
            parsed = parse_sync_sv(text)
            if parsed:
                self._handle_sync_sv(parsed["node_id"], parsed["state_vector"])
            return True

        if text.startswith(SYNC_FRAG_PREFIX):
            parsed = parse_sync_frag(text)
            if parsed:
                self._handle_incoming_fragment(parsed, from_id)
            return True

        if text.startswith(SYNC_ACK_PREFIX):
            parsed = parse_sync_ack(text)
            if parsed:
                self._handle_sync_ack(parsed["session_id"], parsed["seq"])
            return True

        if text.startswith(SYNC_NACK_PREFIX):
            parsed = parse_sync_nack(text)
            if parsed:
                self._handle_sync_nack(parsed["session_id"], parsed["seq"])
            return True

        logger.debug("SyncRelay: unrecognized SYNC_ prefix from %s: %s", from_id, text[:30])
        return True  # Consumed (it was a SYNC_ message, just unknown type)

    # ── Sync orchestration ───────────────────────────────────────────

    def _handle_sync_sv(self, node_id: str, remote_sv: dict[str, int]) -> None:
        """Handle full state vector from edge. Triggers delta fetch from Production."""
        logger.info("SyncRelay: received SYNC_SV from %s: %s", node_id, remote_sv)
        result = self.trigger_sync_for_node(node_id, remote_sv)
        if result.get("status") == "completed":
            self._last_sync_by_node[node_id] = time.monotonic()

    def trigger_sync_for_node(self, node_id: str, remote_sv: dict[str, int]) -> dict:
        """Execute a full sync cycle: fetch delta from Production, fragment, send.

        Args:
            node_id: Target edge node's Meshtastic ID.
            remote_sv: Edge node's state vector {node_id: max_timestamp}.

        Returns:
            Dict with status, session_id, fragment_count, error if any.
        """
        session_id = generate_session_id()
        sv_hash = compute_sv_hash(remote_sv)

        # Log the sync attempt
        log_id = self.db.create_sync_log(
            node_id,
            SyncDirection.TO_EDGE.value,
            session_id=session_id,
            sv_hash_before=sv_hash,
        )

        # Create alert
        self._create_alert(
            node_id,
            AlertType.SYNC_RELAY_STARTED,
            f"Sync relay started for {node_id} (session: {session_id})",
        )

        try:
            # Fetch delta from Production
            delta = self._fetch_delta_from_production(node_id, remote_sv)
            if delta is None:
                self.db.update_sync_log(
                    log_id,
                    status="failed",
                    error="Production API unreachable or returned error",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
                self._create_alert(
                    node_id,
                    AlertType.SYNC_RELAY_FAILED,
                    f"Sync relay failed for {node_id}: Production API error",
                )
                return {"status": "failed", "error": "production_api_error"}

            # Strip content for LoRa (metadata only)
            stripped = self._strip_content_for_lora(delta)

            # Prioritize and queue
            queue_result = self._prioritize_and_queue(node_id, session_id, stripped)

            # Fragment and send
            total_frags = queue_result.get("total_fragments", 0)
            if total_frags == 0:
                # No delta to send — already in sync
                self.db.update_sync_log(
                    log_id,
                    status="completed",
                    items_synced=0,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
                return {"status": "completed", "items": 0, "session_id": session_id}

            # Store session state — one entry per fragment sub-session
            frag_sessions = queue_result.get("frag_sessions", [])
            for frag_sid in frag_sessions:
                frag_count = len(self.db.get_fragments_for_session(frag_sid))
                self._active_sessions[frag_sid] = {
                    "node_id": node_id,
                    "log_id": log_id,
                    "parent_session": session_id,
                    "status": "sending",
                    "total_fragments": frag_count,
                    "acked": set(),
                    "started": time.monotonic(),
                }

            # Send first batch for each fragment sub-session
            for frag_sid in frag_sessions:
                self._send_pending_fragments(frag_sid, node_id)

            items = queue_result.get("total_items", 0)
            self.db.update_sync_log(
                log_id,
                status="completed",
                items_synced=items,
                completed_at=datetime.now(timezone.utc).isoformat(),
                sv_hash_after=compute_sv_hash(delta.get("state_vector", {})),
            )
            self._create_alert(
                node_id,
                AlertType.SYNC_RELAY_COMPLETED,
                f"Sync relay completed for {node_id}: {items} items, "
                f"{total_frags} fragments (session: {session_id})",
            )
            return {
                "status": "completed",
                "session_id": session_id,
                "items": items,
                "fragments": total_frags,
            }

        except Exception as e:
            logger.exception("SyncRelay: error during sync for %s", node_id)
            self.db.update_sync_log(
                log_id,
                status="failed",
                error=str(e),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            self._create_alert(
                node_id,
                AlertType.SYNC_RELAY_FAILED,
                f"Sync relay failed for {node_id}: {e}",
            )
            return {"status": "failed", "error": str(e)}

    def _fetch_delta_from_production(
        self, node_id: str, remote_sv: dict[str, int]
    ) -> Optional[dict]:
        """Call POST /api/v1/sync on Jenn Production to get the delta.

        Returns:
            Delta dict with conversations, memories, config, state_vector, known_ids.
            None if the API call failed.
        """
        if not self._production_url:
            logger.warning("SyncRelay: no production_url configured")
            return None

        url = f"{self._production_url}/api/v1/sync"
        payload = {
            "node_id": node_id,
            "device_id": f"mesh-gateway-{node_id}",
            "state_vector": remote_sv,
            "protocol_version": 1,
        }
        headers = {
            "Content-Type": "application/json",
            "X-Device-ID": f"mesh-gateway-{node_id}",
            "X-Device-Token": self._sync_token,
        }

        try:
            if self._http_client:
                response = self._http_client.post(url, json=payload, headers=headers)
                if hasattr(response, "status_code") and response.status_code == 200:
                    return response.json()
                logger.error(
                    "SyncRelay: Production API returned %s",
                    getattr(response, "status_code", "unknown"),
                )
                return None
            else:
                # No HTTP client configured — return None (tests inject mock)
                logger.warning("SyncRelay: no http_client configured for Production API")
                return None
        except Exception as e:
            logger.error("SyncRelay: Production API call failed: %s", e)
            return None

    def _strip_content_for_lora(self, delta: dict) -> dict:
        """Remove bulky 'data' fields from GSetItems for LoRa transmission.

        Keeps: id, timestamp, node_id, deleted, deleted_at, deleted_by, project_id
        Removes: data (the actual message content — too large for LoRa)
        """
        stripped = dict(delta)
        conversations = stripped.get("conversations", [])
        stripped_convos = []
        for item in conversations:
            slim = {
                "id": item.get("id"),
                "timestamp": item.get("timestamp"),
                "node_id": item.get("node_id"),
            }
            # Only include deletion fields if present
            if item.get("deleted"):
                slim["deleted"] = True
                slim["deleted_at"] = item.get("deleted_at")
                slim["deleted_by"] = item.get("deleted_by")
                if item.get("deleted_wall_time"):
                    slim["deleted_wall_time"] = item["deleted_wall_time"]
            if item.get("project_id"):
                slim["project_id"] = item["project_id"]
            stripped_convos.append(slim)
        stripped["conversations"] = stripped_convos
        return stripped

    def _prioritize_and_queue(self, node_id: str, session_id: str, delta: dict) -> dict:
        """Split delta into priority buckets and create fragment queue entries.

        Each priority bucket gets its own fragment session_id to avoid seq
        collisions (each bucket's fragments start at seq=0). The parent
        session_id links all queue entries together.

        Returns dict with total_fragments, total_items, and frag_sessions list.
        """
        total_items = 0
        total_fragments = 0
        frag_sessions: list[str] = []

        # P1: Tombstones and config (critical)
        tombstones = [c for c in delta.get("conversations", []) if c.get("deleted")]
        config_items = delta.get("config", {})

        # P2: Conversation metadata (non-tombstone)
        metadata = [c for c in delta.get("conversations", []) if not c.get("deleted")]

        # P3: Memories
        memories = delta.get("memories", {})

        # Fragment and queue each priority level
        for priority, data, label in [
            (SyncPriority.CRITICAL.value, tombstones, "tombstones"),
            (SyncPriority.CRITICAL.value, config_items, "config"),
            (SyncPriority.IMPORTANT.value, metadata, "metadata"),
            (SyncPriority.NORMAL.value, memories, "memories"),
        ]:
            if not data:
                continue

            # Each bucket gets its own fragment session to avoid UNIQUE(session,seq)
            frag_session_id = generate_session_id()
            frag_sessions.append(frag_session_id)

            payload = json.dumps(data, separators=(",", ":"))
            item_count = len(data) if isinstance(data, list) else len(data.keys())
            frags = self._fragmenter.fragment(payload, frag_session_id)

            self.db.create_sync_queue_entry(
                node_id=node_id,
                session_id=frag_session_id,
                direction=SyncDirection.TO_EDGE.value,
                payload_json=payload,
                priority=priority,
                total_fragments=len(frags),
            )

            # Store fragments in DB
            for frag in frags:
                self.db.create_sync_fragment(
                    session_id=frag_session_id,
                    seq=frag["seq"],
                    total=frag["total"],
                    direction="outbound",
                    payload_b64=frag["b64_payload"],
                    crc16=frag["crc16"],
                )

            total_items += item_count
            total_fragments += len(frags)
            logger.debug(
                "SyncRelay: queued %d %s items → %d fragments (frag_session=%s)",
                item_count,
                label,
                len(frags),
                frag_session_id,
            )

        return {
            "total_items": total_items,
            "total_fragments": total_fragments,
            "frag_sessions": frag_sessions,
        }

    def _send_pending_fragments(self, session_id: str, node_id: str) -> int:
        """Send all pending fragments for a session via RadioBridge.

        Returns the number of fragments sent.
        """
        if self._bridge is None:
            logger.warning("SyncRelay: no bridge — can't send fragments")
            return 0

        pending = self.db.get_pending_fragments(session_id)
        sent_count = 0

        for frag in pending:
            wire = format_sync_frag(
                session_id,
                frag["seq"],
                frag["total"],
                frag["crc16"],
                frag["payload_b64"],
            )
            try:
                result = self._bridge.send_text(
                    wire,
                    destination=node_id,
                    channel_index=SYNC_CHANNEL_INDEX,
                )
                if result:
                    self.db.update_sync_fragment(
                        frag["id"],
                        status="sent",
                        send_attempts=frag["send_attempts"] + 1,
                    )
                    sent_count += 1
                else:
                    logger.warning(
                        "SyncRelay: bridge returned False for frag %d/%d (session=%s)",
                        frag["seq"],
                        frag["total"],
                        session_id,
                    )
            except Exception as e:
                logger.error("SyncRelay: send error for frag %d: %s", frag["seq"], e)

        return sent_count

    # ── ACK/NACK handling ────────────────────────────────────────────

    def _handle_sync_ack(self, session_id: str, seq: int) -> None:
        """Mark fragment as ACKed. Complete session if all ACKed."""
        session = self._active_sessions.get(session_id)
        if session is None:
            logger.debug("SyncRelay: ACK for unknown session %s", session_id)
            return

        session["acked"].add(seq)

        # Update fragment in DB
        frags = self.db.get_fragments_for_session(session_id, direction="outbound")
        for frag in frags:
            if frag["seq"] == seq:
                self.db.update_sync_fragment(
                    frag["id"],
                    status="acked",
                    acked_at=datetime.now(timezone.utc).isoformat(),
                )
                break

        # Check if session complete
        if len(session["acked"]) >= session["total_fragments"]:
            session["status"] = "completed"
            logger.info("SyncRelay: session %s fully ACKed", session_id)
            self._last_sync_by_node[session["node_id"]] = time.monotonic()

    def _handle_sync_nack(self, session_id: str, seq: int) -> None:
        """Retransmit the NACKed fragment."""
        session = self._active_sessions.get(session_id)
        if session is None:
            return

        frags = self.db.get_fragments_for_session(session_id, direction="outbound")
        for frag in frags:
            if frag["seq"] == seq and frag["send_attempts"] < MAX_RETRANSMITS:
                self.db.update_sync_fragment(frag["id"], status="nacked")
                # Retransmit
                self._send_single_fragment(session_id, session["node_id"], frag)
                break

    def _send_single_fragment(self, session_id: str, node_id: str, frag: dict) -> bool:
        """Send a single fragment over LoRa."""
        if self._bridge is None:
            return False
        wire = format_sync_frag(
            session_id, frag["seq"], frag["total"], frag["crc16"], frag["payload_b64"]
        )
        try:
            result = self._bridge.send_text(
                wire, destination=node_id, channel_index=SYNC_CHANNEL_INDEX
            )
            if result:
                self.db.update_sync_fragment(
                    frag["id"],
                    status="sent",
                    send_attempts=frag["send_attempts"] + 1,
                )
            return bool(result)
        except Exception as e:
            logger.error("SyncRelay: retransmit error: %s", e)
            return False

    # ── Edge push relay (edge → production) ──────────────────────────

    def _handle_incoming_fragment(self, parsed: dict, from_id: str) -> None:
        """Reassemble incoming fragments from edge. Relay to Production when complete."""
        result = self._reassembler.add_fragment(
            parsed["session_id"],
            parsed["seq"],
            parsed["total"],
            parsed["b64_payload"],
            parsed["crc16"],
        )

        if result is None:
            # Not complete yet — ACK the fragment
            self._send_ack(from_id, parsed["session_id"], parsed["seq"])
            return

        if result.get("error"):
            # CRC mismatch or decode error — NACK
            self._send_nack(from_id, parsed["session_id"], parsed["seq"])
            return

        if result.get("complete"):
            # All fragments received — ACK the last one and relay to Production
            self._send_ack(from_id, parsed["session_id"], parsed["seq"])
            self._relay_edge_push_to_production(from_id, parsed["session_id"], result["payload"])

    def _send_ack(self, node_id: str, session_id: str, seq: int) -> None:
        """Send SYNC_ACK over LoRa."""
        from jenn_mesh.models.sync_relay import format_sync_ack

        if self._bridge is None:
            return
        wire = format_sync_ack(session_id, seq)
        try:
            self._bridge.send_text(wire, destination=node_id, channel_index=SYNC_CHANNEL_INDEX)
        except Exception as e:
            logger.error("SyncRelay: ACK send error: %s", e)

    def _send_nack(self, node_id: str, session_id: str, seq: int) -> None:
        """Send SYNC_NACK over LoRa."""
        from jenn_mesh.models.sync_relay import format_sync_nack

        if self._bridge is None:
            return
        wire = format_sync_nack(session_id, seq)
        try:
            self._bridge.send_text(wire, destination=node_id, channel_index=SYNC_CHANNEL_INDEX)
        except Exception as e:
            logger.error("SyncRelay: NACK send error: %s", e)

    def _relay_edge_push_to_production(
        self, node_id: str, session_id: str, payload_json: str
    ) -> None:
        """Relay reassembled edge push to Jenn Production POST /api/v1/sync/push."""
        if not self._production_url:
            logger.warning("SyncRelay: no production_url — can't relay push")
            return

        log_id = self.db.create_sync_log(
            node_id,
            SyncDirection.FROM_EDGE.value,
            session_id=session_id,
        )

        try:
            conversations = json.loads(payload_json)
            url = f"{self._production_url}/api/v1/sync/push"
            push_payload = {
                "node_id": node_id,
                "device_id": f"mesh-edge-{node_id}",
                "conversations": conversations if isinstance(conversations, list) else [],
            }
            headers = {
                "Content-Type": "application/json",
                "X-Device-ID": f"mesh-edge-{node_id}",
                "X-Device-Token": self._sync_token,
            }

            if self._http_client:
                response = self._http_client.post(url, json=push_payload, headers=headers)
                if hasattr(response, "status_code") and response.status_code == 200:
                    result = response.json()
                    self.db.update_sync_log(
                        log_id,
                        status="completed",
                        items_synced=result.get("accepted", 0),
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )
                    logger.info(
                        "SyncRelay: relayed %d items from %s to Production",
                        result.get("accepted", 0),
                        node_id,
                    )
                    return
                logger.error(
                    "SyncRelay: push relay failed: %s", getattr(response, "status_code", "unknown")
                )

            self.db.update_sync_log(
                log_id,
                status="failed",
                error="HTTP client error or not configured",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        except Exception as e:
            logger.exception("SyncRelay: push relay error for %s", node_id)
            self.db.update_sync_log(
                log_id,
                status="failed",
                error=str(e),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    # ── Query / status ───────────────────────────────────────────────

    def get_sync_status(self) -> dict:
        """Get overall sync relay status summary."""
        active_count = sum(
            1 for s in self._active_sessions.values() if s.get("status") == "sending"
        )
        pending = self.db.get_pending_sync_entries()
        return {
            "active_sessions": active_count,
            "pending_queue_entries": len(pending),
            "known_nodes": len(self._known_sv_hashes),
            "reassembler_sessions": self._reassembler.active_sessions,
            "cooldown_minutes": self.cooldown_minutes,
        }

    def get_node_sync_history(self, node_id: str, limit: int = 20) -> list[dict]:
        """Get sync log history for a specific node."""
        return self.db.get_sync_log_for_node(node_id, limit=limit)

    # ── Alert helper ─────────────────────────────────────────────────

    def _create_alert(self, node_id: str, alert_type: AlertType, message: str) -> None:
        """Create a fleet alert for sync events."""
        from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP

        severity = ALERT_SEVERITY_MAP.get(alert_type, AlertSeverity.INFO)
        try:
            self.db.create_alert(
                node_id=node_id,
                alert_type=alert_type.value,
                severity=severity.value,
                message=message,
            )
        except Exception as e:
            logger.error("SyncRelay: failed to create alert: %s", e)
