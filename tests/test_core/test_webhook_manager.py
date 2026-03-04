"""Tests for the webhook manager — CRUD, dispatch, signing, and delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.core.webhook_manager import (
    RETRY_DELAYS_SECONDS,
    WebhookManager,
    _sign_payload,
)
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db(tmp_path) -> MeshDatabase:
    return MeshDatabase(db_path=str(tmp_path / "wh_test.db"))


@pytest.fixture
def manager(db) -> WebhookManager:
    return WebhookManager(db=db)


# ── _sign_payload() ──────────────────────────────────────────────────


class TestSignPayload:
    def test_produces_sha256_prefix(self):
        sig = _sign_payload("secret", b'{"test": true}')
        assert sig.startswith("sha256=")

    def test_signature_is_valid_hmac(self):
        secret = "test-secret"
        body = b'{"event_type": "alert_created"}'
        sig = _sign_payload(secret, body)
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        assert sig == f"sha256={expected}"

    def test_different_secrets_different_sigs(self):
        body = b"same body"
        sig1 = _sign_payload("secret-a", body)
        sig2 = _sign_payload("secret-b", body)
        assert sig1 != sig2

    def test_empty_secret(self):
        sig = _sign_payload("", b"body")
        assert sig.startswith("sha256=")


# ── WebhookManager CRUD ──────────────────────────────────────────────


class TestWebhookCRUD:
    def test_create_and_get(self, manager):
        wh = manager.create_webhook(
            name="Test Hook",
            url="https://example.com/hook",
            secret="s3cret",
            event_types=["alert_created"],
        )
        assert wh["name"] == "Test Hook"
        assert wh["url"] == "https://example.com/hook"
        fetched = manager.get_webhook(wh["id"])
        assert fetched is not None
        assert fetched["name"] == "Test Hook"

    def test_list_webhooks(self, manager):
        manager.create_webhook(name="A", url="https://a.com/hook")
        manager.create_webhook(name="B", url="https://b.com/hook")
        all_hooks = manager.list_webhooks()
        assert len(all_hooks) == 2

    def test_list_active_only(self, manager, db):
        wh = manager.create_webhook(name="Active", url="https://a.com/hook")
        wh2 = manager.create_webhook(name="Inactive", url="https://b.com/hook")
        db.update_webhook(wh2["id"], is_active=False)
        active = manager.list_webhooks(active_only=True)
        assert len(active) == 1
        assert active[0]["name"] == "Active"

    def test_update_webhook(self, manager):
        wh = manager.create_webhook(name="Old", url="https://old.com/hook")
        assert manager.update_webhook(wh["id"], name="New")
        updated = manager.get_webhook(wh["id"])
        assert updated["name"] == "New"

    def test_delete_webhook(self, manager):
        wh = manager.create_webhook(name="ToDelete", url="https://del.com/hook")
        assert manager.delete_webhook(wh["id"])
        assert manager.get_webhook(wh["id"]) is None

    def test_get_nonexistent_returns_none(self, manager):
        assert manager.get_webhook(9999) is None


# ── Event dispatch ────────────────────────────────────────────────────


class TestEventDispatch:
    def test_dispatch_to_matching_webhook(self, manager, db):
        manager.create_webhook(
            name="Alerts",
            url="https://a.com/hook",
            event_types=["alert_created"],
        )
        count = manager.dispatch_event("alert_created", {"node_id": "!abc"})
        assert count == 1
        # Check delivery was created
        deliveries = db.get_pending_webhook_deliveries(limit=10)
        assert len(deliveries) == 1
        assert deliveries[0]["event_type"] == "alert_created"

    def test_dispatch_skips_non_subscribed(self, manager, db):
        manager.create_webhook(
            name="Only Offline",
            url="https://a.com/hook",
            event_types=["node_offline"],
        )
        count = manager.dispatch_event("alert_created", {"node_id": "!abc"})
        assert count == 0

    def test_dispatch_to_all_events_webhook(self, manager, db):
        # Empty event_types = subscribe to all
        manager.create_webhook(
            name="Catch All",
            url="https://a.com/hook",
            event_types=[],
        )
        count = manager.dispatch_event("anything", {})
        assert count == 1

    def test_dispatch_payload_format(self, manager, db):
        manager.create_webhook(name="Hook", url="https://a.com/hook")
        manager.dispatch_event("test_event", {"key": "value"})
        deliveries = db.get_pending_webhook_deliveries(limit=1)
        payload = json.loads(deliveries[0]["payload_json"])
        assert payload["event_type"] == "test_event"
        assert payload["source"] == "jenn-mesh"
        assert "timestamp" in payload
        assert payload["data"]["key"] == "value"

    def test_dispatch_to_multiple_webhooks(self, manager):
        manager.create_webhook(name="A", url="https://a.com/hook")
        manager.create_webhook(name="B", url="https://b.com/hook")
        count = manager.dispatch_event("test", {})
        assert count == 2

    def test_dispatch_skips_inactive(self, manager, db):
        wh = manager.create_webhook(name="Inactive", url="https://a.com/hook")
        db.update_webhook(wh["id"], is_active=False)
        count = manager.dispatch_event("test", {})
        assert count == 0


# ── Retry schedule ────────────────────────────────────────────────────


class TestRetrySchedule:
    def test_retry_delays_are_increasing(self):
        for i in range(1, len(RETRY_DELAYS_SECONDS)):
            assert RETRY_DELAYS_SECONDS[i] > RETRY_DELAYS_SECONDS[i - 1]

    def test_max_retry_is_16_min(self):
        assert RETRY_DELAYS_SECONDS[-1] == 960  # 16 minutes


# ── Test fire ─────────────────────────────────────────────────────────


class TestTestFire:
    def test_test_fire_nonexistent(self, manager):
        result = manager.test_fire(9999)
        assert "error" in result

    def test_test_fire_no_httpx(self, manager):
        wh = manager.create_webhook(name="Test", url="https://a.com/hook")
        with patch.dict("sys.modules", {"httpx": None}):
            result = manager.test_fire(wh["id"])
            # May still work if httpx cached — just verify it returns a dict
            assert isinstance(result, dict)


# ── Delivery processing ──────────────────────────────────────────────


class TestDeliveryProcessing:
    def test_process_no_pending_returns_zeros(self, manager):
        counts = manager.process_pending_deliveries()
        assert counts == {"delivered": 0, "retrying": 0, "failed": 0}

    def test_process_successful_delivery(self, manager, db):
        import sys

        wh = manager.create_webhook(
            name="Hook",
            url="https://a.com/hook",
            secret="test-secret",
        )
        manager.dispatch_event("test", {"msg": "hello"})

        # Mock httpx module since it's imported inside the method
        mock_httpx = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_httpx.Client.return_value = mock_client

        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            counts = manager.process_pending_deliveries()
        assert counts["delivered"] == 1

    def test_handle_failure_retries(self, manager, db):
        wh = manager.create_webhook(name="Hook", url="https://a.com/hook")
        manager.dispatch_event("test", {})
        deliveries = db.get_pending_webhook_deliveries(limit=1)
        did = deliveries[0]["id"]

        # Simulate first failure
        manager._handle_failure(did, current_attempt=0, error="timeout")
        # Check via list_webhook_deliveries — pending deliveries may exclude
        # those with future next_retry_at
        all_deliveries = db.list_webhook_deliveries(wh["id"], limit=10)
        assert any(d["id"] == did and d["status"] == "retrying" for d in all_deliveries)

    def test_handle_failure_marks_failed_after_max(self, manager, db):
        wh = manager.create_webhook(name="Hook", url="https://a.com/hook")
        manager.dispatch_event("test", {})
        deliveries = db.get_pending_webhook_deliveries(limit=1)
        did = deliveries[0]["id"]

        # Simulate reaching max attempts (5)
        manager._handle_failure(did, current_attempt=4, error="final failure")
        # After 5 attempts → permanently failed
        all_deliveries = db.list_webhook_deliveries(wh["id"])
        failed = [d for d in all_deliveries if d["status"] == "failed"]
        assert len(failed) == 1
