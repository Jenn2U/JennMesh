"""Sync relay models — wire format, enums, and helpers for CRDT sync over LoRa mesh."""

from __future__ import annotations

import hashlib
import json
import secrets
from enum import Enum
from typing import Optional

# Channel 1 (ADMIN) — PSK-encrypted, same as recovery commands
SYNC_CHANNEL_INDEX = 1

# Wire format prefixes
SYNC_SV_PREFIX = "SYNC_SV|"
SYNC_REQ_PREFIX = "SYNC_REQ|"
SYNC_FRAG_PREFIX = "SYNC_FRAG|"
SYNC_ACK_PREFIX = "SYNC_ACK|"
SYNC_NACK_PREFIX = "SYNC_NACK|"
SYNC_META_PREFIX = "SYNC_META|"

# Fragment sizing: LoRa max 256 bytes, ~200 usable after Meshtastic framing,
# ~40 bytes header overhead per SYNC_FRAG → ~140 bytes payload per fragment
MAX_FRAG_PAYLOAD_BYTES = 140

# Reassembly timeout: 5 minutes (fragments may arrive slowly over mesh)
REASSEMBLY_TIMEOUT_SECONDS = 300

# Session IDs: 6-char hex (unique per sync exchange)
SESSION_ID_LENGTH = 6

# Retransmission limits
MAX_RETRANSMITS = 3


class SyncDirection(str, Enum):
    """Direction of a sync exchange."""

    TO_EDGE = "to_edge"
    FROM_EDGE = "from_edge"
    SV_EXCHANGE = "sv_exchange"


class SyncSessionStatus(str, Enum):
    """Lifecycle states for a sync session."""

    PENDING = "pending"
    SENDING = "sending"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class SyncFragmentStatus(str, Enum):
    """Lifecycle states for an individual fragment."""

    PENDING = "pending"
    SENT = "sent"
    ACKED = "acked"
    NACKED = "nacked"
    TIMEOUT = "timeout"


class SyncPriority(int, Enum):
    """Priority levels for sync data over LoRa.

    P1: Tombstones, config LWW, production-authoritative keys → immediate
    P2: Conversation metadata (id, timestamp, node_id, deleted flag) → batched
    P3: Memories (LWW values) → if bandwidth allows
    """

    CRITICAL = 1
    IMPORTANT = 2
    NORMAL = 3


# ── Wire format helpers ──────────────────────────────────────────


def generate_session_id() -> str:
    """Generate a 6-character hex session ID."""
    return secrets.token_hex(SESSION_ID_LENGTH // 2)


def compute_sv_hash(state_vector: dict[str, int]) -> str:
    """Compute an 8-char hash of a state vector for heartbeat piggyback.

    Deterministic: same state vector always produces the same hash.
    Compact: fits in a single heartbeat field.

    Args:
        state_vector: {node_id: max_lamport_timestamp} dict.

    Returns:
        8-character hex string.
    """
    # Sort keys for deterministic serialization
    canonical = json.dumps(state_vector, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:8]


def compute_crc16(data: bytes) -> str:
    """Compute CRC-16/CCITT for fragment integrity verification.

    Args:
        data: Raw bytes to checksum.

    Returns:
        4-character hex string (16-bit CRC).
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return f"{crc:04x}"


# ── SYNC_SV: State vector exchange ──────────────────────────────


def format_sync_sv(node_id: str, state_vector: dict[str, int]) -> str:
    """Build SYNC_SV wire message for full state vector transmission.

    Format: SYNC_SV|{node_id}|{sv_json}
    Example: SYNC_SV|!28979058|{"production":1523,"!a1b2c3d4":847}

    Args:
        node_id: Sending device's Meshtastic node ID.
        state_vector: {node_id: max_timestamp} dict.

    Returns:
        Pipe-delimited wire text.
    """
    sv_json = json.dumps(state_vector, separators=(",", ":"))
    return f"SYNC_SV|{node_id}|{sv_json}"


def parse_sync_sv(text: str) -> Optional[dict]:
    """Parse a SYNC_SV message from mesh text.

    Returns:
        Dict with node_id and state_vector, or None if invalid.
    """
    if not text.startswith(SYNC_SV_PREFIX):
        return None

    # Split into exactly 3 parts (JSON may not contain pipes, but be safe)
    parts = text.split("|", 2)
    if len(parts) != 3:
        return None

    try:
        return {
            "node_id": parts[1],
            "state_vector": json.loads(parts[2]),
        }
    except (json.JSONDecodeError, IndexError):
        return None


# ── SYNC_REQ: Delta announcement ────────────────────────────────


def format_sync_req(session_id: str, total_frags: int, priority: int) -> str:
    """Build SYNC_REQ wire message announcing an incoming fragmented delta.

    Format: SYNC_REQ|{session_id}|{total_frags}|{priority}
    """
    return f"SYNC_REQ|{session_id}|{total_frags}|{priority}"


def parse_sync_req(text: str) -> Optional[dict]:
    """Parse a SYNC_REQ message.

    Returns:
        Dict with session_id, total_frags, priority, or None if invalid.
    """
    if not text.startswith(SYNC_REQ_PREFIX):
        return None

    parts = text.split("|")
    if len(parts) != 4:
        return None

    try:
        return {
            "session_id": parts[1],
            "total_frags": int(parts[2]),
            "priority": int(parts[3]),
        }
    except (ValueError, IndexError):
        return None


# ── SYNC_FRAG: Fragment transmission ─────────────────────────────


def format_sync_frag(session_id: str, seq: int, total: int, crc16: str, b64_payload: str) -> str:
    """Build SYNC_FRAG wire message for a single fragment.

    Format: SYNC_FRAG|{session_id}|{seq}|{total}|{crc16}|{b64_payload}
    """
    return f"SYNC_FRAG|{session_id}|{seq}|{total}|{crc16}|{b64_payload}"


def parse_sync_frag(text: str) -> Optional[dict]:
    """Parse a SYNC_FRAG message.

    Returns:
        Dict with session_id, seq, total, crc16, b64_payload, or None if invalid.
    """
    if not text.startswith(SYNC_FRAG_PREFIX):
        return None

    # Split into exactly 6 parts (b64 payload won't contain pipes)
    parts = text.split("|", 5)
    if len(parts) != 6:
        return None

    try:
        return {
            "session_id": parts[1],
            "seq": int(parts[2]),
            "total": int(parts[3]),
            "crc16": parts[4],
            "b64_payload": parts[5],
        }
    except (ValueError, IndexError):
        return None


# ── SYNC_ACK / SYNC_NACK: Fragment acknowledgment ───────────────


def format_sync_ack(session_id: str, seq: int) -> str:
    """Build SYNC_ACK wire message for a received fragment.

    Format: SYNC_ACK|{session_id}|{seq}
    """
    return f"SYNC_ACK|{session_id}|{seq}"


def parse_sync_ack(text: str) -> Optional[dict]:
    """Parse a SYNC_ACK message.

    Returns:
        Dict with session_id and seq, or None if invalid.
    """
    if not text.startswith(SYNC_ACK_PREFIX):
        return None

    parts = text.split("|")
    if len(parts) != 3:
        return None

    try:
        return {
            "session_id": parts[1],
            "seq": int(parts[2]),
        }
    except (ValueError, IndexError):
        return None


def format_sync_nack(session_id: str, seq: int) -> str:
    """Build SYNC_NACK wire message requesting retransmission of a fragment.

    Format: SYNC_NACK|{session_id}|{seq}
    """
    return f"SYNC_NACK|{session_id}|{seq}"


def parse_sync_nack(text: str) -> Optional[dict]:
    """Parse a SYNC_NACK message.

    Returns:
        Dict with session_id and seq, or None if invalid.
    """
    if not text.startswith(SYNC_NACK_PREFIX):
        return None

    parts = text.split("|")
    if len(parts) != 3:
        return None

    try:
        return {
            "session_id": parts[1],
            "seq": int(parts[2]),
        }
    except (ValueError, IndexError):
        return None


# ── SYNC_META: Single metadata update ───────────────────────────


def format_sync_meta(node_id: str, key: str, value: str) -> str:
    """Build SYNC_META wire message for a single small metadata update.

    Format: SYNC_META|{node_id}|{key}|{value}
    Used for tombstones and small LWW values that fit in one message.
    """
    # Truncate value to keep total under 200 bytes
    max_val_len = 200 - len(f"SYNC_META|{node_id}|{key}|")
    if len(value) > max_val_len:
        value = value[:max_val_len]
    return f"SYNC_META|{node_id}|{key}|{value}"


def parse_sync_meta(text: str) -> Optional[dict]:
    """Parse a SYNC_META message.

    Returns:
        Dict with node_id, key, value, or None if invalid.
    """
    if not text.startswith(SYNC_META_PREFIX):
        return None

    # Split into exactly 4 parts (value may contain pipes)
    parts = text.split("|", 3)
    if len(parts) != 4:
        return None

    try:
        return {
            "node_id": parts[1],
            "key": parts[2],
            "value": parts[3],
        }
    except IndexError:
        return None
