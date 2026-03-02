"""Tests for channel and security models."""

import pytest

from jenn_mesh.models.channel import (
    AdminKeyConfig,
    ChannelConfig,
    ChannelRole,
    ChannelSet,
    SecurityConfig,
)


class TestChannelConfig:
    def test_generate_psk_256(self):
        psk = ChannelConfig.generate_psk(256)
        assert psk.startswith("0x")
        assert len(psk) == 66  # "0x" + 64 hex chars = 32 bytes

    def test_generate_psk_128(self):
        psk = ChannelConfig.generate_psk(128)
        assert psk.startswith("0x")
        assert len(psk) == 34  # "0x" + 32 hex chars = 16 bytes

    def test_generate_psk_invalid_bits_raises(self):
        with pytest.raises(ValueError, match="128 or 256"):
            ChannelConfig.generate_psk(192)

    def test_generate_psk_uniqueness(self):
        psk1 = ChannelConfig.generate_psk(256)
        psk2 = ChannelConfig.generate_psk(256)
        assert psk1 != psk2  # Cryptographically random, never equal


class TestChannelSet:
    def test_get_primary(self):
        cs = ChannelSet(
            channels=[
                ChannelConfig(index=0, name="JennMesh", role=ChannelRole.PRIMARY, psk="0xabc"),
                ChannelConfig(index=1, name="Admin", role=ChannelRole.ADMIN, psk="0xdef"),
            ]
        )
        primary = cs.get_primary()
        assert primary is not None
        assert primary.name == "JennMesh"

    def test_get_admin(self):
        cs = ChannelSet(
            channels=[
                ChannelConfig(index=0, name="JennMesh", role=ChannelRole.PRIMARY, psk="0xabc"),
                ChannelConfig(index=1, name="Admin", role=ChannelRole.ADMIN, psk="0xdef"),
            ]
        )
        admin = cs.get_admin()
        assert admin is not None
        assert admin.role == ChannelRole.ADMIN

    def test_empty_channel_set(self):
        cs = ChannelSet()
        assert cs.get_primary() is None
        assert cs.get_admin() is None


class TestSecurityConfig:
    def test_max_three_admin_keys(self):
        sc = SecurityConfig(
            admin_keys=[
                AdminKeyConfig(public_key="key1"),
                AdminKeyConfig(public_key="key2"),
                AdminKeyConfig(public_key="key3"),
            ]
        )
        assert len(sc.admin_keys) == 3

    def test_managed_mode_default_off(self):
        sc = SecurityConfig()
        assert sc.is_managed is False
        assert sc.admin_channel_enabled is False
