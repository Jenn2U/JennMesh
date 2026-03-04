"""Tests for sync relay wire format models, helpers, and validation."""

from jenn_mesh.models.sync_relay import (
    MAX_FRAG_PAYLOAD_BYTES,
    REASSEMBLY_TIMEOUT_SECONDS,
    SESSION_ID_LENGTH,
    SYNC_CHANNEL_INDEX,
    SYNC_META_PREFIX,
    SYNC_SV_PREFIX,
    SyncDirection,
    SyncFragmentStatus,
    SyncPriority,
    SyncSessionStatus,
    compute_crc16,
    compute_sv_hash,
    format_sync_ack,
    format_sync_frag,
    format_sync_meta,
    format_sync_nack,
    format_sync_req,
    format_sync_sv,
    generate_session_id,
    parse_sync_ack,
    parse_sync_frag,
    parse_sync_meta,
    parse_sync_nack,
    parse_sync_req,
    parse_sync_sv,
)


class TestConstants:
    """Verify sync relay constants."""

    def test_admin_channel(self):
        assert SYNC_CHANNEL_INDEX == 1

    def test_fragment_payload_size(self):
        assert MAX_FRAG_PAYLOAD_BYTES == 140

    def test_reassembly_timeout(self):
        assert REASSEMBLY_TIMEOUT_SECONDS == 300

    def test_session_id_length(self):
        assert SESSION_ID_LENGTH == 6


class TestEnums:
    """Verify sync relay enums."""

    def test_sync_direction_values(self):
        assert SyncDirection.TO_EDGE == "to_edge"
        assert SyncDirection.FROM_EDGE == "from_edge"
        assert SyncDirection.SV_EXCHANGE == "sv_exchange"

    def test_sync_session_status_values(self):
        assert SyncSessionStatus.PENDING == "pending"
        assert SyncSessionStatus.SENDING == "sending"
        assert SyncSessionStatus.COMPLETED == "completed"
        assert SyncSessionStatus.FAILED == "failed"
        assert SyncSessionStatus.TIMEOUT == "timeout"

    def test_sync_fragment_status_values(self):
        assert SyncFragmentStatus.PENDING == "pending"
        assert SyncFragmentStatus.SENT == "sent"
        assert SyncFragmentStatus.ACKED == "acked"
        assert SyncFragmentStatus.NACKED == "nacked"
        assert SyncFragmentStatus.TIMEOUT == "timeout"

    def test_sync_priority_ordering(self):
        assert SyncPriority.CRITICAL < SyncPriority.IMPORTANT < SyncPriority.NORMAL
        assert SyncPriority.CRITICAL == 1
        assert SyncPriority.IMPORTANT == 2
        assert SyncPriority.NORMAL == 3


class TestGenerateSessionId:
    """Verify session ID generation."""

    def test_length(self):
        sid = generate_session_id()
        assert len(sid) == SESSION_ID_LENGTH

    def test_hex_chars_only(self):
        sid = generate_session_id()
        assert all(c in "0123456789abcdef" for c in sid)

    def test_uniqueness(self):
        ids = {generate_session_id() for _ in range(100)}
        assert len(ids) == 100


class TestComputeSvHash:
    """Verify state vector hashing."""

    def test_deterministic(self):
        sv = {"production": 1523, "!a1b2c3d4": 847}
        h1 = compute_sv_hash(sv)
        h2 = compute_sv_hash(sv)
        assert h1 == h2

    def test_length(self):
        sv = {"production": 100}
        assert len(compute_sv_hash(sv)) == 8

    def test_hex_chars(self):
        sv = {"node1": 1}
        h = compute_sv_hash(sv)
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_vectors_different_hashes(self):
        h1 = compute_sv_hash({"production": 1523})
        h2 = compute_sv_hash({"production": 1524})
        assert h1 != h2

    def test_key_order_independent(self):
        h1 = compute_sv_hash({"a": 1, "b": 2})
        h2 = compute_sv_hash({"b": 2, "a": 1})
        assert h1 == h2

    def test_empty_vector(self):
        h = compute_sv_hash({})
        assert len(h) == 8


class TestComputeCrc16:
    """Verify CRC-16/CCITT implementation."""

    def test_known_value(self):
        # CRC-16/CCITT of "123456789" is 0x29B1
        crc = compute_crc16(b"123456789")
        assert crc == "29b1"

    def test_empty_input(self):
        crc = compute_crc16(b"")
        assert len(crc) == 4
        assert crc == "ffff"  # CRC-16/CCITT init value

    def test_deterministic(self):
        data = b"hello world"
        assert compute_crc16(data) == compute_crc16(data)

    def test_different_data_different_crc(self):
        assert compute_crc16(b"hello") != compute_crc16(b"world")

    def test_hex_format(self):
        crc = compute_crc16(b"test")
        assert len(crc) == 4
        int(crc, 16)  # Should not raise


class TestSyncSvFormat:
    """Verify SYNC_SV wire format."""

    def test_format(self):
        result = format_sync_sv("!28979058", {"production": 1523, "!a1b2c3d4": 847})
        assert result.startswith(SYNC_SV_PREFIX)
        assert "!28979058" in result

    def test_roundtrip(self):
        sv = {"production": 1523, "!a1b2c3d4": 847}
        wire = format_sync_sv("!28979058", sv)
        parsed = parse_sync_sv(wire)
        assert parsed is not None
        assert parsed["node_id"] == "!28979058"
        assert parsed["state_vector"] == sv

    def test_parse_invalid_prefix(self):
        assert parse_sync_sv("RECOVER|something") is None

    def test_parse_too_few_parts(self):
        assert parse_sync_sv("SYNC_SV|nodeonly") is None

    def test_parse_invalid_json(self):
        assert parse_sync_sv("SYNC_SV|node|not-json") is None


class TestSyncReqFormat:
    """Verify SYNC_REQ wire format."""

    def test_format(self):
        result = format_sync_req("a3f0b2", 5, 1)
        assert result == "SYNC_REQ|a3f0b2|5|1"

    def test_roundtrip(self):
        wire = format_sync_req("a3f0b2", 10, 2)
        parsed = parse_sync_req(wire)
        assert parsed is not None
        assert parsed["session_id"] == "a3f0b2"
        assert parsed["total_frags"] == 10
        assert parsed["priority"] == 2

    def test_parse_invalid_prefix(self):
        assert parse_sync_req("SYNC_SV|something") is None

    def test_parse_wrong_part_count(self):
        assert parse_sync_req("SYNC_REQ|a3f0b2|5") is None

    def test_parse_non_numeric(self):
        assert parse_sync_req("SYNC_REQ|a3f0b2|abc|1") is None


class TestSyncFragFormat:
    """Verify SYNC_FRAG wire format."""

    def test_format(self):
        result = format_sync_frag("a3f0b2", 0, 3, "1a2b", "dGVzdA==")
        assert result == "SYNC_FRAG|a3f0b2|0|3|1a2b|dGVzdA=="

    def test_roundtrip(self):
        wire = format_sync_frag("a3f0b2", 2, 5, "abcd", "cGF5bG9hZA==")
        parsed = parse_sync_frag(wire)
        assert parsed is not None
        assert parsed["session_id"] == "a3f0b2"
        assert parsed["seq"] == 2
        assert parsed["total"] == 5
        assert parsed["crc16"] == "abcd"
        assert parsed["b64_payload"] == "cGF5bG9hZA=="

    def test_parse_invalid_prefix(self):
        assert parse_sync_frag("NOT_FRAG|something") is None

    def test_parse_wrong_part_count(self):
        assert parse_sync_frag("SYNC_FRAG|a|b|c") is None


class TestSyncAckFormat:
    """Verify SYNC_ACK wire format."""

    def test_format(self):
        assert format_sync_ack("a3f0b2", 3) == "SYNC_ACK|a3f0b2|3"

    def test_roundtrip(self):
        wire = format_sync_ack("a3f0b2", 7)
        parsed = parse_sync_ack(wire)
        assert parsed is not None
        assert parsed["session_id"] == "a3f0b2"
        assert parsed["seq"] == 7

    def test_parse_invalid_prefix(self):
        assert parse_sync_ack("SYNC_NACK|something|1") is None

    def test_parse_non_numeric_seq(self):
        assert parse_sync_ack("SYNC_ACK|a3f0b2|abc") is None


class TestSyncNackFormat:
    """Verify SYNC_NACK wire format."""

    def test_format(self):
        assert format_sync_nack("a3f0b2", 2) == "SYNC_NACK|a3f0b2|2"

    def test_roundtrip(self):
        wire = format_sync_nack("a3f0b2", 4)
        parsed = parse_sync_nack(wire)
        assert parsed is not None
        assert parsed["session_id"] == "a3f0b2"
        assert parsed["seq"] == 4

    def test_parse_invalid_prefix(self):
        assert parse_sync_nack("SYNC_ACK|something|1") is None


class TestSyncMetaFormat:
    """Verify SYNC_META wire format."""

    def test_format(self):
        result = format_sync_meta("!a1b2", "tombstone:msg123", "1|1709395200|!a1b2")
        assert result.startswith(SYNC_META_PREFIX)
        assert "!a1b2" in result
        assert "tombstone:msg123" in result

    def test_roundtrip(self):
        wire = format_sync_meta("!a1b2", "config:role", "relay")
        parsed = parse_sync_meta(wire)
        assert parsed is not None
        assert parsed["node_id"] == "!a1b2"
        assert parsed["key"] == "config:role"
        assert parsed["value"] == "relay"

    def test_value_with_pipes(self):
        """Value field may contain pipes (split with maxsplit=3)."""
        wire = format_sync_meta("!a1b2", "data", "a|b|c")
        parsed = parse_sync_meta(wire)
        assert parsed is not None
        assert parsed["value"] == "a|b|c"

    def test_truncation(self):
        """Long values are truncated to fit 200-byte LoRa limit."""
        long_value = "x" * 500
        wire = format_sync_meta("!a1b2", "key", long_value)
        assert len(wire) <= 200

    def test_parse_invalid_prefix(self):
        assert parse_sync_meta("RECOVER|something") is None

    def test_parse_too_few_parts(self):
        assert parse_sync_meta("SYNC_META|node|key") is None
