"""Tests for sync fragmenter — split and reassemble payloads for LoRa."""

import base64
import json
import time

from jenn_mesh.core.sync_fragmenter import SyncFragmenter, SyncReassembler
from jenn_mesh.models.sync_relay import MAX_FRAG_PAYLOAD_BYTES, compute_crc16


class TestSyncFragmenter:
    """Tests for SyncFragmenter.fragment()."""

    def setup_method(self):
        self.fragmenter = SyncFragmenter()

    def test_small_payload_single_fragment(self):
        """Payload under MAX_FRAG_PAYLOAD_BYTES → 1 fragment."""
        payload = json.dumps({"key": "value"})
        frags = self.fragmenter.fragment(payload, "a1b2c3")
        assert len(frags) == 1
        assert frags[0]["seq"] == 0
        assert frags[0]["total"] == 1

    def test_fragment_count_for_large_payload(self):
        """Payload 3x the limit → 3 fragments."""
        payload = "x" * (MAX_FRAG_PAYLOAD_BYTES * 3)
        frags = self.fragmenter.fragment(payload, "a1b2c3")
        assert len(frags) == 3

    def test_fragment_count_rounds_up(self):
        """Payload slightly over 1x limit → 2 fragments."""
        payload = "x" * (MAX_FRAG_PAYLOAD_BYTES + 1)
        frags = self.fragmenter.fragment(payload, "a1b2c3")
        assert len(frags) == 2

    def test_fragment_sequence_numbers(self):
        """Fragments are numbered 0..N-1."""
        payload = "x" * (MAX_FRAG_PAYLOAD_BYTES * 4)
        frags = self.fragmenter.fragment(payload, "a1b2c3")
        assert [f["seq"] for f in frags] == [0, 1, 2, 3]
        assert all(f["total"] == 4 for f in frags)

    def test_fragment_crc16_valid(self):
        """Each fragment has a valid CRC-16 matching its payload."""
        payload = json.dumps({"data": "hello world " * 50})
        frags = self.fragmenter.fragment(payload, "a1b2c3")
        for frag in frags:
            chunk = base64.b64decode(frag["b64_payload"])
            assert compute_crc16(chunk) == frag["crc16"]

    def test_fragment_wire_text_format(self):
        """Wire text starts with SYNC_FRAG| prefix."""
        payload = "test"
        frags = self.fragmenter.fragment(payload, "a1b2c3")
        assert frags[0]["wire_text"].startswith("SYNC_FRAG|a1b2c3|")

    def test_reassemble_matches_original(self):
        """Fragmenting then reassembling yields the original payload."""
        payload = json.dumps({"conversations": [{"id": f"msg-{i}"} for i in range(50)]})
        frags = self.fragmenter.fragment(payload, "sess01")

        # Reassemble manually
        chunks = []
        for frag in frags:
            chunks.append(base64.b64decode(frag["b64_payload"]))
        reassembled = b"".join(chunks).decode("utf-8")
        assert reassembled == payload

    def test_empty_payload(self):
        """Empty string still produces 1 fragment."""
        frags = self.fragmenter.fragment("", "a1b2c3")
        assert len(frags) == 1
        assert frags[0]["seq"] == 0

    def test_unicode_payload(self):
        """Unicode characters are properly encoded/fragmented."""
        payload = json.dumps({"emoji": "🔥" * 100})
        frags = self.fragmenter.fragment(payload, "a1b2c3")
        assert len(frags) >= 1
        # Verify roundtrip
        chunks = [base64.b64decode(f["b64_payload"]) for f in frags]
        assert b"".join(chunks).decode("utf-8") == payload

    def test_wire_text_under_256_bytes(self):
        """Each wire_text message fits within LoRa 256-byte limit."""
        payload = "x" * 1000
        frags = self.fragmenter.fragment(payload, "a1b2c3")
        for frag in frags:
            assert len(frag["wire_text"].encode("utf-8")) <= 256


class TestSyncReassembler:
    """Tests for SyncReassembler."""

    def setup_method(self):
        self.reassembler = SyncReassembler(timeout_seconds=300)

    def test_single_fragment_completes(self):
        """One fragment with total=1 completes immediately."""
        data = b"hello"
        b64 = base64.b64encode(data).decode()
        crc = compute_crc16(data)

        result = self.reassembler.add_fragment("sess01", 0, 1, b64, crc)
        assert result is not None
        assert result["complete"] is True
        assert result["payload"] == "hello"

    def test_multi_fragment_completes_on_last(self):
        """Multiple fragments complete only when all received."""
        payload = "hello world test data"
        raw = payload.encode("utf-8")

        # Split into 2 chunks
        mid = len(raw) // 2
        chunk0, chunk1 = raw[:mid], raw[mid:]
        b64_0, crc_0 = base64.b64encode(chunk0).decode(), compute_crc16(chunk0)
        b64_1, crc_1 = base64.b64encode(chunk1).decode(), compute_crc16(chunk1)

        # First fragment → not complete
        result = self.reassembler.add_fragment("sess01", 0, 2, b64_0, crc_0)
        assert result is None

        # Second fragment → complete
        result = self.reassembler.add_fragment("sess01", 1, 2, b64_1, crc_1)
        assert result is not None
        assert result["complete"] is True
        assert result["payload"] == payload

    def test_out_of_order_fragments(self):
        """Fragments received out of order still reassemble correctly."""
        payload = "abcdefghijklmnop"
        raw = payload.encode("utf-8")
        mid = len(raw) // 2
        chunk0, chunk1 = raw[:mid], raw[mid:]
        b64_0, crc_0 = base64.b64encode(chunk0).decode(), compute_crc16(chunk0)
        b64_1, crc_1 = base64.b64encode(chunk1).decode(), compute_crc16(chunk1)

        # Receive seq=1 first, then seq=0
        result = self.reassembler.add_fragment("sess01", 1, 2, b64_1, crc_1)
        assert result is None
        result = self.reassembler.add_fragment("sess01", 0, 2, b64_0, crc_0)
        assert result is not None
        assert result["complete"] is True
        assert result["payload"] == payload

    def test_crc_mismatch_returns_error(self):
        """Bad CRC produces error result."""
        data = b"hello"
        b64 = base64.b64encode(data).decode()

        result = self.reassembler.add_fragment("sess01", 0, 1, b64, "0000")
        assert result is not None
        assert result["complete"] is False
        assert result["error"] == "crc_mismatch"
        assert result["seq"] == 0

    def test_invalid_base64_returns_error(self):
        """Corrupt base64 produces error result."""
        result = self.reassembler.add_fragment("sess01", 0, 1, "!!!invalid!!!", "0000")
        assert result is not None
        assert result["complete"] is False
        assert result["error"] == "decode_failed"

    def test_multiple_sessions(self):
        """Can track multiple sessions concurrently."""
        data_a = b"session_a"
        data_b = b"session_b"

        self.reassembler.add_fragment(
            "sessA", 0, 2, base64.b64encode(data_a).decode(), compute_crc16(data_a)
        )
        self.reassembler.add_fragment(
            "sessB", 0, 2, base64.b64encode(data_b).decode(), compute_crc16(data_b)
        )

        assert self.reassembler.active_sessions == 2

    def test_completed_session_cleaned_up(self):
        """Completed session is removed from tracking."""
        data = b"done"
        b64 = base64.b64encode(data).decode()
        crc = compute_crc16(data)

        self.reassembler.add_fragment("sess01", 0, 1, b64, crc)
        assert self.reassembler.active_sessions == 0

    def test_timeout_detection(self):
        """Stale sessions are detected and removed."""
        data = b"pending"
        b64 = base64.b64encode(data).decode()
        crc = compute_crc16(data)

        self.reassembler.add_fragment("sess01", 0, 3, b64, crc)
        assert self.reassembler.active_sessions == 1

        # Simulate timeout by adjusting start time
        self.reassembler._sessions["sess01"]["started"] = time.monotonic() - 301

        timed_out = self.reassembler.check_timeouts()
        assert "sess01" in timed_out
        assert self.reassembler.active_sessions == 0

    def test_no_timeout_within_window(self):
        """Fresh sessions are not timed out."""
        data = b"fresh"
        b64 = base64.b64encode(data).decode()
        crc = compute_crc16(data)

        self.reassembler.add_fragment("sess01", 0, 3, b64, crc)
        timed_out = self.reassembler.check_timeouts()
        assert timed_out == []

    def test_get_session_status(self):
        """Can query status of in-progress session."""
        data = b"partial"
        b64 = base64.b64encode(data).decode()
        crc = compute_crc16(data)

        self.reassembler.add_fragment("sess01", 0, 5, b64, crc)
        status = self.reassembler.get_session_status("sess01")
        assert status is not None
        assert status["received"] == 1
        assert status["total"] == 5

    def test_get_session_status_unknown(self):
        """Unknown session returns None."""
        assert self.reassembler.get_session_status("unknown") is None

    def test_duplicate_fragment_idempotent(self):
        """Receiving the same fragment twice doesn't break anything."""
        data = b"hello"
        b64 = base64.b64encode(data).decode()
        crc = compute_crc16(data)

        self.reassembler.add_fragment("sess01", 0, 2, b64, crc)
        self.reassembler.add_fragment("sess01", 0, 2, b64, crc)

        status = self.reassembler.get_session_status("sess01")
        assert status["received"] == 1  # Deduped (dict key overwrite)


class TestFragmenterReassemblerIntegration:
    """End-to-end tests: fragment → reassemble."""

    def setup_method(self):
        self.fragmenter = SyncFragmenter()
        self.reassembler = SyncReassembler()

    def test_small_payload_roundtrip(self):
        """Small JSON payload survives fragment→reassemble."""
        payload = json.dumps({"state_vector": {"production": 1523}})
        frags = self.fragmenter.fragment(payload, "integ1")

        for frag in frags:
            result = self.reassembler.add_fragment(
                "integ1", frag["seq"], frag["total"], frag["b64_payload"], frag["crc16"]
            )
        assert result is not None
        assert result["complete"] is True
        assert json.loads(result["payload"]) == json.loads(payload)

    def test_large_payload_roundtrip(self):
        """Large multi-fragment payload survives roundtrip."""
        conversations = [
            {
                "id": f"msg-{i}",
                "timestamp": 1000 + i,
                "node_id": "!a1b2c3d4",
                "deleted": False,
            }
            for i in range(50)
        ]
        payload = json.dumps({"conversations": conversations})
        frags = self.fragmenter.fragment(payload, "integ2")
        assert len(frags) > 1  # Must be multi-fragment

        result = None
        for frag in frags:
            result = self.reassembler.add_fragment(
                "integ2", frag["seq"], frag["total"], frag["b64_payload"], frag["crc16"]
            )

        assert result is not None
        assert result["complete"] is True
        assert json.loads(result["payload"]) == json.loads(payload)

    def test_corrupted_fragment_detected(self):
        """Tampered fragment is caught by CRC check."""
        payload = json.dumps({"data": "important"})
        frags = self.fragmenter.fragment(payload, "integ3")

        # Tamper with the b64 payload
        tampered_b64 = base64.b64encode(b"TAMPERED").decode()
        result = self.reassembler.add_fragment(
            "integ3",
            frags[0]["seq"],
            frags[0]["total"],
            tampered_b64,
            frags[0]["crc16"],  # Original CRC won't match
        )
        assert result is not None
        assert result["error"] == "crc_mismatch"

    def test_reversed_order_roundtrip(self):
        """Fragments in reverse order still reassemble correctly."""
        payload = json.dumps({"nodes": list(range(100))})
        frags = self.fragmenter.fragment(payload, "integ4")

        result = None
        for frag in reversed(frags):
            result = self.reassembler.add_fragment(
                "integ4", frag["seq"], frag["total"], frag["b64_payload"], frag["crc16"]
            )

        assert result is not None
        assert result["complete"] is True
        assert json.loads(result["payload"]) == json.loads(payload)
