"""Tests for the notification dispatcher — multi-channel alert routing."""

from __future__ import annotations

import json
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.core.notification_dispatcher import (
    NotificationDispatcher,
    _format_email,
    _format_slack,
    _format_teams,
)
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db(tmp_path) -> MeshDatabase:
    return MeshDatabase(db_path=str(tmp_path / "notif_test.db"))


@pytest.fixture
def dispatcher(db) -> NotificationDispatcher:
    return NotificationDispatcher(db=db)


# ── Formatters ────────────────────────────────────────────────────────


class TestSlackFormatter:
    def test_format_includes_blocks(self):
        payload = _format_slack("low_battery", "warning", {"node_id": "!abc"})
        assert "blocks" in payload
        assert len(payload["blocks"]) == 2

    def test_format_severity_emoji_critical(self):
        payload = _format_slack("test", "critical", {})
        header_text = payload["blocks"][0]["text"]["text"]
        assert "🔴" in header_text

    def test_format_severity_emoji_warning(self):
        payload = _format_slack("test", "warning", {})
        header_text = payload["blocks"][0]["text"]["text"]
        assert "🟡" in header_text

    def test_format_severity_emoji_info(self):
        payload = _format_slack("test", "info", {})
        header_text = payload["blocks"][0]["text"]["text"]
        assert "🔵" in header_text

    def test_format_includes_node_id(self):
        payload = _format_slack("test", "info", {"node_id": "!xyz"})
        fields = payload["blocks"][1]["fields"]
        node_field = [f for f in fields if "!xyz" in f.get("text", "")]
        assert len(node_field) > 0

    def test_format_default_node_unknown(self):
        payload = _format_slack("test", "info", {})
        fields = payload["blocks"][1]["fields"]
        node_field = [f for f in fields if "unknown" in f.get("text", "")]
        assert len(node_field) > 0


class TestTeamsFormatter:
    def test_format_includes_adaptive_card(self):
        payload = _format_teams("test", "warning", {})
        assert "attachments" in payload
        content = payload["attachments"][0]["content"]
        assert content["type"] == "AdaptiveCard"

    def test_format_color_mapping(self):
        payload = _format_teams("test", "critical", {})
        body = payload["attachments"][0]["content"]["body"]
        assert body[0]["color"] == "attention"

    def test_format_includes_facts(self):
        payload = _format_teams("test", "info", {"node_id": "!abc"})
        facts = payload["attachments"][0]["content"]["body"][1]["facts"]
        assert any(f["title"] == "Node" and f["value"] == "!abc" for f in facts)


class TestEmailFormatter:
    def test_format_includes_all_fields(self):
        body = _format_email("low_battery", "warning", {"node_id": "!abc"})
        assert "low_battery" in body
        assert "warning" in body
        assert "!abc" in body

    def test_format_has_header(self):
        body = _format_email("test", "info", {})
        assert "JennMesh Fleet Alert" in body


# ── NotificationDispatcher ────────────────────────────────────────────


class TestNotificationDispatcher:
    def _create_channel_and_rule(self, db, channel_type="slack"):
        config = json.dumps({"webhook_url": "https://hooks.slack.com/test"})
        ch_id = db.create_notification_channel(
            name="Test Channel",
            channel_type=channel_type,
            config_json=config,
        )
        db.create_notification_rule(
            name="All Criticals",
            alert_types=json.dumps([]),  # match all
            severities=json.dumps(["critical"]),
            channel_ids=json.dumps([ch_id]),
        )
        return ch_id

    def test_notify_no_rules_returns_zero(self, dispatcher):
        count = dispatcher.notify("low_battery", "critical", {"node_id": "!abc"})
        assert count == 0

    def test_notify_matching_rule(self, db, dispatcher):
        self._create_channel_and_rule(db)
        with patch.object(dispatcher, "_send_slack") as mock_send:
            count = dispatcher.notify("low_battery", "critical", {"node_id": "!abc"})
        assert count == 1
        mock_send.assert_called_once()

    def test_notify_non_matching_severity(self, db, dispatcher):
        self._create_channel_and_rule(db)
        # Rule matches critical only — info should not match
        count = dispatcher.notify("test", "info", {})
        assert count == 0

    def test_notify_handles_send_failure(self, db, dispatcher):
        self._create_channel_and_rule(db)
        with patch.object(dispatcher, "_send_slack", side_effect=Exception("fail")):
            # Should not raise — just log and continue
            count = dispatcher.notify("test", "critical", {})
        assert count == 0

    def test_notify_teams_channel(self, db, dispatcher):
        config = json.dumps({"webhook_url": "https://teams.example.com/hook"})
        ch_id = db.create_notification_channel(
            name="Teams", channel_type="teams", config_json=config
        )
        db.create_notification_rule(
            name="Teams Rule",
            alert_types=json.dumps([]),
            severities=json.dumps([]),  # match all severities
            channel_ids=json.dumps([ch_id]),
        )
        with patch.object(dispatcher, "_send_teams") as mock_send:
            count = dispatcher.notify("test", "info", {})
        assert count == 1
        mock_send.assert_called_once()

    def test_notify_webhook_channel_delegates(self, db):
        mock_wh = MagicMock()
        dispatcher = NotificationDispatcher(db=db, webhook_manager=mock_wh)
        config = json.dumps({"url": "https://example.com/hook"})
        ch_id = db.create_notification_channel(
            name="WH", channel_type="webhook", config_json=config
        )
        db.create_notification_rule(
            name="WH Rule",
            alert_types=json.dumps([]),
            severities=json.dumps([]),
            channel_ids=json.dumps([ch_id]),
        )
        count = dispatcher.notify("test", "info", {})
        assert count == 1
        mock_wh.dispatch_event.assert_called_once()

    def test_inactive_channel_excluded(self, db, dispatcher):
        ch_id = db.create_notification_channel(
            name="Disabled",
            channel_type="slack",
            config_json=json.dumps({"webhook_url": "https://hooks.slack.com/x"}),
        )
        db.update_notification_channel(ch_id, is_active=False)
        db.create_notification_rule(
            name="Rule",
            alert_types=json.dumps([]),
            severities=json.dumps([]),
            channel_ids=json.dumps([ch_id]),
        )
        count = dispatcher.notify("test", "critical", {})
        assert count == 0

    def test_email_channel(self, db, dispatcher):
        config = json.dumps({
            "smtp_host": "localhost",
            "smtp_port": 1025,
            "from_address": "noreply@jenn2u.ai",
            "to_addresses": ["admin@jenn2u.ai"],
            "use_tls": False,
        })
        ch_id = db.create_notification_channel(
            name="Email", channel_type="email", config_json=config
        )
        db.create_notification_rule(
            name="Email Rule",
            alert_types=json.dumps([]),
            severities=json.dumps([]),
            channel_ids=json.dumps([ch_id]),
        )
        with patch.object(dispatcher, "_send_email") as mock_send:
            count = dispatcher.notify("test", "critical", {})
        assert count == 1
        mock_send.assert_called_once()
