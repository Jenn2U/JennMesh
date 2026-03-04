"""Sync fragmenter — split and reassemble JSON payloads for LoRa transmission."""

from __future__ import annotations

import base64
import logging
import math
import time
from typing import Optional

from jenn_mesh.models.sync_relay import (
    MAX_FRAG_PAYLOAD_BYTES,
    REASSEMBLY_TIMEOUT_SECONDS,
    compute_crc16,
    format_sync_frag,
)

logger = logging.getLogger(__name__)


class SyncFragmenter:
    """Split JSON payloads into LoRa-sized fragments with CRC-16 integrity.

    Each fragment carries a base64-encoded chunk of the original UTF-8 payload,
    plus a CRC-16 checksum for corruption detection. Fragments are sequenced
    0..N-1 with session_id binding them together.
    """

    def fragment(self, payload_json: str, session_id: str) -> list[dict]:
        """Split a JSON string into base64-encoded fragments.

        Args:
            payload_json: The full JSON payload to fragment.
            session_id: 6-char hex session ID binding fragments together.

        Returns:
            List of dicts, each containing:
                seq: Fragment sequence number (0-indexed).
                total: Total fragment count.
                b64_payload: Base64-encoded chunk.
                crc16: CRC-16/CCITT hex of the raw chunk bytes.
                wire_text: Ready-to-send SYNC_FRAG wire message.
        """
        raw_bytes = payload_json.encode("utf-8")
        total = max(1, math.ceil(len(raw_bytes) / MAX_FRAG_PAYLOAD_BYTES))

        fragments: list[dict] = []
        for seq in range(total):
            start = seq * MAX_FRAG_PAYLOAD_BYTES
            end = start + MAX_FRAG_PAYLOAD_BYTES
            chunk = raw_bytes[start:end]

            b64 = base64.b64encode(chunk).decode("ascii")
            crc = compute_crc16(chunk)
            wire = format_sync_frag(session_id, seq, total, crc, b64)

            fragments.append(
                {
                    "seq": seq,
                    "total": total,
                    "b64_payload": b64,
                    "crc16": crc,
                    "wire_text": wire,
                }
            )

        logger.debug(
            "Fragmented %d bytes into %d fragments (session=%s)",
            len(raw_bytes),
            total,
            session_id,
        )
        return fragments


class SyncReassembler:
    """Reassemble incoming LoRa fragments into complete JSON payloads.

    Tracks multiple concurrent sessions, verifies CRC-16 per fragment,
    and detects timeouts for incomplete sessions.
    """

    def __init__(self, timeout_seconds: int = REASSEMBLY_TIMEOUT_SECONDS):
        self.timeout_seconds = timeout_seconds
        # session_id → {"fragments": {seq: bytes}, "total": int, "started": float}
        self._sessions: dict[str, dict] = {}

    def add_fragment(
        self,
        session_id: str,
        seq: int,
        total: int,
        b64_payload: str,
        crc16: str,
    ) -> Optional[dict]:
        """Add a received fragment and check for completion.

        Args:
            session_id: Session binding fragments together.
            seq: Fragment sequence number (0-indexed).
            total: Total expected fragments.
            b64_payload: Base64-encoded chunk.
            crc16: Expected CRC-16 hex of the raw chunk.

        Returns:
            - If all fragments received: {"complete": True, "payload": <json_str>}
            - If CRC mismatch: {"complete": False, "error": "crc_mismatch", "seq": seq}
            - If more fragments needed: None
        """
        # Decode and verify CRC
        try:
            chunk = base64.b64decode(b64_payload)
        except Exception:
            return {"complete": False, "error": "decode_failed", "seq": seq}

        actual_crc = compute_crc16(chunk)
        if actual_crc != crc16:
            logger.warning(
                "CRC mismatch for session=%s seq=%d: expected=%s actual=%s",
                session_id,
                seq,
                crc16,
                actual_crc,
            )
            return {"complete": False, "error": "crc_mismatch", "seq": seq}

        # Initialize or update session
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "fragments": {},
                "total": total,
                "started": time.monotonic(),
            }

        session = self._sessions[session_id]
        session["fragments"][seq] = chunk

        # Check if complete
        if len(session["fragments"]) >= session["total"]:
            # Reassemble in order
            ordered = b"".join(session["fragments"][i] for i in range(session["total"]))
            payload = ordered.decode("utf-8")
            del self._sessions[session_id]
            logger.debug(
                "Reassembled %d fragments into %d bytes (session=%s)",
                session["total"],
                len(ordered),
                session_id,
            )
            return {"complete": True, "payload": payload}

        return None

    def check_timeouts(self) -> list[str]:
        """Check for sessions that have exceeded the reassembly timeout.

        Returns:
            List of timed-out session_ids (removed from tracking).
        """
        now = time.monotonic()
        timed_out: list[str] = []

        for session_id, session in list(self._sessions.items()):
            elapsed = now - session["started"]
            if elapsed > self.timeout_seconds:
                received = len(session["fragments"])
                total = session["total"]
                logger.warning(
                    "Reassembly timeout for session=%s: %d/%d fragments after %.0fs",
                    session_id,
                    received,
                    total,
                    elapsed,
                )
                timed_out.append(session_id)
                del self._sessions[session_id]

        return timed_out

    @property
    def active_sessions(self) -> int:
        """Number of sessions currently being reassembled."""
        return len(self._sessions)

    def get_session_status(self, session_id: str) -> Optional[dict]:
        """Get status of a reassembly session.

        Returns:
            Dict with received count, total, and elapsed time, or None.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return {
            "received": len(session["fragments"]),
            "total": session["total"],
            "elapsed_seconds": time.monotonic() - session["started"],
        }
