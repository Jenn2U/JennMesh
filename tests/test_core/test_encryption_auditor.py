"""Tests for the encryption auditor — PSK classification and fleet scoring."""

from __future__ import annotations

import tempfile

import pytest

from jenn_mesh.core.encryption_auditor import (
    EMPTY_PSKS,
    EncryptionAuditor,
    classify_psk_strength,
)
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.encryption import EncryptionStatus

# ── classify_psk_strength() unit tests ────────────────────────────────


class TestClassifyPskStrength:
    """Test PSK classification logic."""

    @pytest.mark.parametrize(
        "psk",
        ["", "0x", "0x00", "0x01", "AQ==", "AA==", None],
    )
    def test_empty_or_default_is_unencrypted(self, psk):
        result = classify_psk_strength(psk or "")
        assert result == EncryptionStatus.UNENCRYPTED

    def test_short_hex_psk_is_weak(self):
        # 8 hex chars = 4 bytes — too short for AES
        assert classify_psk_strength("0x12345678") == EncryptionStatus.WEAK

    def test_short_hex_15_bytes_is_weak(self):
        # 30 hex chars = 15 bytes — still below AES-128
        assert classify_psk_strength("0x" + "ab" * 15) == EncryptionStatus.WEAK

    def test_aes128_hex_is_strong(self):
        # 32 hex chars = 16 bytes = AES-128
        assert classify_psk_strength("0x" + "ab" * 16) == EncryptionStatus.STRONG

    def test_aes256_hex_is_strong(self):
        # 64 hex chars = 32 bytes = AES-256
        assert classify_psk_strength("0x" + "ff" * 32) == EncryptionStatus.STRONG

    def test_long_base64_is_strong(self):
        # 24+ char base64 ≈ 18+ bytes
        psk = "YWJjZGVmZ2hpamtsbW5vcHFyc3Q="  # 28 chars
        assert classify_psk_strength(psk) == EncryptionStatus.STRONG

    def test_short_base64_is_weak(self):
        # 4-23 char base64 — non-empty but too short
        assert classify_psk_strength("ABCD") == EncryptionStatus.WEAK

    def test_very_short_base64_is_unencrypted(self):
        # Less than 4 chars, not in EMPTY_PSKS
        assert classify_psk_strength("AB") == EncryptionStatus.UNENCRYPTED

    def test_whitespace_handling(self):
        # Whitespace around empty should be unencrypted
        assert classify_psk_strength("  AQ==  ") == EncryptionStatus.UNENCRYPTED

    def test_uppercase_hex_prefix(self):
        assert classify_psk_strength("0X" + "ab" * 16) == EncryptionStatus.STRONG

    def test_empty_psks_set_complete(self):
        """Verify known weak PSKs are captured in the constant."""
        assert "AQ==" in EMPTY_PSKS  # LongFast base64
        assert "0x01" in EMPTY_PSKS  # LongFast hex
        assert "" in EMPTY_PSKS


# ── EncryptionAuditor integration tests ───────────────────────────────


@pytest.fixture
def audit_db(tmp_path) -> MeshDatabase:
    """DB with devices and channel data for encryption audit testing."""
    db = MeshDatabase(db_path=str(tmp_path / "enc_test.db"))
    # Register two devices
    db.upsert_device("!enc111", long_name="Relay-1", role="ROUTER")
    db.upsert_device("!enc222", long_name="Mobile-2", role="CLIENT")
    return db


def _add_channel(db: MeshDatabase, index: int, name: str, psk: str) -> None:
    """Helper to insert a channel row directly."""
    with db.connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO channels (channel_index, name, psk) VALUES (?, ?, ?)",
            (index, name, psk),
        )


class TestEncryptionAuditor:
    def test_all_strong_fleet_score_100(self, audit_db):
        _add_channel(audit_db, 0, "Primary", "0x" + "ab" * 32)
        auditor = EncryptionAuditor(audit_db)
        report = auditor.audit_fleet()
        assert report.fleet_score == 100.0
        assert report.strong_count == 2
        assert report.unencrypted_count == 0

    def test_default_psk_fleet_score_0(self, audit_db):
        _add_channel(audit_db, 0, "LongFast", "AQ==")
        auditor = EncryptionAuditor(audit_db)
        report = auditor.audit_fleet()
        assert report.fleet_score == 0.0
        assert report.unencrypted_count == 2

    def test_mixed_fleet_score(self, audit_db):
        # Strong primary + weak secondary
        _add_channel(audit_db, 0, "Primary", "0x" + "ab" * 32)
        _add_channel(audit_db, 1, "Admin", "0x1234")
        auditor = EncryptionAuditor(audit_db)
        report = auditor.audit_fleet()
        # Both devices have at least one weak channel → worst status is weak
        assert report.fleet_score == 0.0
        assert report.weak_count == 2

    def test_single_device_audit(self, audit_db):
        _add_channel(audit_db, 0, "Primary", "AQ==")
        auditor = EncryptionAuditor(audit_db)
        audit = auditor.audit_device("!enc111")
        assert audit.encryption_status == EncryptionStatus.UNENCRYPTED
        assert audit.uses_default_longfast is True
        assert len(audit.weak_channels) == 1

    def test_no_channels_returns_unknown(self, audit_db):
        auditor = EncryptionAuditor(audit_db)
        audit = auditor.audit_device("!enc111")
        assert audit.encryption_status == EncryptionStatus.UNKNOWN
        assert audit.channel_count == 0

    def test_empty_fleet_score_is_100(self, tmp_path):
        db = MeshDatabase(db_path=str(tmp_path / "empty.db"))
        auditor = EncryptionAuditor(db)
        assert auditor.get_fleet_encryption_score() == 100.0

    def test_fleet_report_device_count(self, audit_db):
        _add_channel(audit_db, 0, "Primary", "0x" + "ab" * 16)
        auditor = EncryptionAuditor(audit_db)
        report = auditor.audit_fleet()
        assert report.total_devices == 2
        assert len(report.devices) == 2

    def test_get_fleet_encryption_score_shortcut(self, audit_db):
        _add_channel(audit_db, 0, "Primary", "0x" + "ab" * 32)
        auditor = EncryptionAuditor(audit_db)
        score = auditor.get_fleet_encryption_score()
        assert score == 100.0
