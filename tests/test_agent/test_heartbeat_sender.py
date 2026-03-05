"""Tests for HeartbeatSender — message building, interval gating, sending."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from jenn_mesh.agent.heartbeat_sender import HeartbeatSender


@pytest.fixture
def bridge():
    mock = MagicMock()
    mock.send_text = MagicMock(return_value=True)
    return mock


@pytest.fixture
def sender(bridge):
    return HeartbeatSender(node_id="!aaa11111", bridge=bridge, interval=120)


# ── Message building ─────────────────────────────────────────────────


class TestBuildMessage:
    def test_basic_message_format(self, sender: HeartbeatSender):
        msg = sender.build_message(uptime_seconds=3600, services="edge:ok,mqtt:down", battery=85)
        assert msg.startswith("HEARTBEAT|!aaa11111|3600|edge:ok,mqtt:down|85|")

    def test_empty_services(self, sender: HeartbeatSender):
        msg = sender.build_message(uptime_seconds=100)
        assert "|100||" in msg

    def test_unknown_battery(self, sender: HeartbeatSender):
        msg = sender.build_message(uptime_seconds=100, battery=-1)
        assert "|-1|" in msg

    def test_message_size_under_256_bytes(self, sender: HeartbeatSender):
        """LoRa has a 256-byte limit — heartbeat should be well under."""
        msg = sender.build_message(
            uptime_seconds=999999,
            services="edge:ok,mqtt:down,internet:down,radio:ok",
            battery=100,
        )
        assert len(msg.encode("utf-8")) < 256


# ── Interval gating ──────────────────────────────────────────────────


class TestIntervalGating:
    def test_should_send_initially(self, sender: HeartbeatSender):
        """First call should always send (last_sent = -inf)."""
        assert sender.should_send() is True

    def test_should_not_send_after_recent(self, sender: HeartbeatSender):
        sender._last_sent = time.monotonic()
        assert sender.should_send() is False

    def test_should_send_after_interval(self, sender: HeartbeatSender):
        sender._last_sent = time.monotonic() - 121
        assert sender.should_send() is True

    def test_maybe_send_gates_correctly(self, sender: HeartbeatSender, bridge):
        # First call: should send
        result = sender.maybe_send(uptime_seconds=100, battery=50)
        assert result is True
        bridge.send_text.assert_called_once()

        # Second immediate call: should be gated
        result = sender.maybe_send(uptime_seconds=101, battery=50)
        assert result is False
        assert bridge.send_text.call_count == 1


# ── Sending ──────────────────────────────────────────────────────────


class TestSending:
    def test_successful_send(self, sender: HeartbeatSender, bridge):
        result = sender.send(uptime_seconds=100, battery=50)
        assert result is True
        assert sender.send_count == 1
        bridge.send_text.assert_called_once()
        msg = bridge.send_text.call_args[0][0]
        assert msg.startswith("HEARTBEAT|!aaa11111|100|")

    def test_failed_send(self, sender: HeartbeatSender, bridge):
        bridge.send_text.return_value = False
        result = sender.send(uptime_seconds=100)
        assert result is False
        assert sender.send_count == 0

    def test_exception_during_send(self, sender: HeartbeatSender, bridge):
        bridge.send_text.side_effect = Exception("Radio error")
        result = sender.send(uptime_seconds=100)
        assert result is False
        assert sender.send_count == 0


# ── Services from health ─────────────────────────────────────────────


class TestServicesFromHealth:
    def test_all_ok(self, sender: HeartbeatSender):
        report = MagicMock(radio_connected=True, mqtt_connected=True)
        result = sender.build_services_from_health(report)
        assert result == "edge:ok,radio:ok,mqtt:ok"

    def test_mqtt_down(self, sender: HeartbeatSender):
        report = MagicMock(radio_connected=True, mqtt_connected=False)
        result = sender.build_services_from_health(report)
        assert "mqtt:down" in result

    def test_radio_down(self, sender: HeartbeatSender):
        report = MagicMock(radio_connected=False, mqtt_connected=True)
        result = sender.build_services_from_health(report)
        assert "radio:down" in result
