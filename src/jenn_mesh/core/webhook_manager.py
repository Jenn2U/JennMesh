"""Webhook Manager — CRUD, event dispatch, HMAC signing, and retry delivery.

Provides:
- CRUD for webhook registrations
- Event dispatch: match event → subscribed webhooks → enqueue deliveries
- HMAC-SHA256 request signing (GitHub/Stripe pattern)
- Exponential backoff retry (30s → 16min, 5 attempts)
- test_fire() for endpoint verification
- Background delivery loop (started by lifespan)

Usage (production)::

    wh_manager = WebhookManager(db=app.state.db)
    task = asyncio.create_task(webhook_delivery_loop_task(wh_manager))

Usage (event dispatch)::

    wh_manager.dispatch_event("alert_created", {"node_id": "!abc", "type": "low_battery"})
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)

# Retry schedule: attempt 1 at +30s, 2 at +2min, 3 at +8min, 4 at +16min
RETRY_DELAYS_SECONDS = [30, 120, 480, 960]

DELIVERY_LOOP_SLEEP_SECONDS = 30


def _sign_payload(secret: str, body: bytes) -> str:
    """Compute HMAC-SHA256 signature for a webhook payload.

    Uses body-only signing (GitHub/Stripe pattern) — the recipient
    recomputes the HMAC over the raw body using the shared secret
    and compares with the ``X-JennMesh-Signature`` header.

    Args:
        secret: Shared HMAC secret (UTF-8 string).
        body:   Raw JSON payload bytes.

    Returns:
        Hex-encoded HMAC-SHA256 digest prefixed with ``sha256=``.
    """
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


class WebhookManager:
    """Manages webhook lifecycle: registration, dispatch, and delivery."""

    def __init__(self, db: MeshDatabase) -> None:
        self.db = db

    # ── CRUD (delegates to db) ────────────────────────────────────────

    def create_webhook(
        self,
        name: str,
        url: str,
        secret: str = "",
        event_types: Optional[list[str]] = None,
    ) -> dict:
        """Register a new webhook. Returns the created record."""
        wh_id = self.db.create_webhook(
            name=name,
            url=url,
            secret=secret,
            event_types=json.dumps(event_types or []),
        )
        return self.db.get_webhook(wh_id) or {"id": wh_id}

    def get_webhook(self, webhook_id: int) -> Optional[dict]:
        return self.db.get_webhook(webhook_id)

    def list_webhooks(self, active_only: bool = False) -> list[dict]:
        return self.db.list_webhooks(active_only=active_only)

    def update_webhook(self, webhook_id: int, **kwargs: Any) -> bool:
        # Serialize event_types list to JSON string for DB
        if "event_types" in kwargs and isinstance(kwargs["event_types"], list):
            kwargs["event_types"] = json.dumps(kwargs["event_types"])
        return self.db.update_webhook(webhook_id, **kwargs)

    def delete_webhook(self, webhook_id: int) -> bool:
        return self.db.delete_webhook(webhook_id)

    # ── Event dispatch ────────────────────────────────────────────────

    def dispatch_event(self, event_type: str, data: dict[str, Any]) -> int:
        """Dispatch an event to all matching webhooks.

        Creates a pending delivery record for each matching webhook.
        The background delivery loop picks these up for actual HTTP POST.

        Returns the number of deliveries enqueued.
        """
        webhooks = self.db.list_webhooks(active_only=True)
        payload = {
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
            "source": "jenn-mesh",
        }
        payload_json = json.dumps(payload, default=str)

        enqueued = 0
        for wh in webhooks:
            # Check if this webhook subscribes to this event type
            subscribed = json.loads(wh.get("event_types", "[]"))
            if subscribed and event_type not in subscribed:
                continue

            self.db.create_webhook_delivery(
                webhook_id=wh["id"],
                event_type=event_type,
                payload_json=payload_json,
            )
            enqueued += 1

        if enqueued:
            logger.info(
                "Dispatched event '%s' to %d webhook(s)", event_type, enqueued
            )
        return enqueued

    # ── Delivery processing ───────────────────────────────────────────

    def process_pending_deliveries(self) -> dict[str, int]:
        """Process all pending deliveries (synchronous).

        Called by the background loop.  For each pending delivery:
        1. POST payload to webhook URL with HMAC signature header
        2. On success (2xx) → mark delivered
        3. On failure → increment attempt, schedule next retry with backoff
        4. On max attempts exceeded → mark failed

        Returns counts of delivered, retrying, and failed.
        """
        try:
            import httpx
        except ImportError:
            logger.warning("httpx not installed — webhook delivery disabled")
            return {"delivered": 0, "retrying": 0, "failed": 0}

        pending = self.db.get_pending_webhook_deliveries(limit=20)
        counts = {"delivered": 0, "retrying": 0, "failed": 0}

        for delivery in pending:
            delivery_id = delivery["id"]
            url = delivery["url"]
            secret = delivery.get("secret", "")
            payload_bytes = delivery["payload_json"].encode("utf-8")
            attempt = delivery.get("attempt_count", 0)

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "JennMesh-Webhook/1.0",
            }
            if secret:
                headers["X-JennMesh-Signature"] = _sign_payload(secret, payload_bytes)

            try:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, content=payload_bytes, headers=headers)

                if 200 <= resp.status_code < 300:
                    self.db.update_webhook_delivery(
                        delivery_id,
                        status="delivered",
                        http_status=resp.status_code,
                        delivered_at=datetime.now(timezone.utc).isoformat(),
                        increment_attempt=True,
                    )
                    counts["delivered"] += 1
                else:
                    self._handle_failure(
                        delivery_id, attempt,
                        http_status=resp.status_code,
                        error=f"HTTP {resp.status_code}",
                    )
                    counts["retrying" if attempt + 1 < 5 else "failed"] += 1

            except Exception as exc:
                self._handle_failure(
                    delivery_id, attempt,
                    error=str(exc)[:500],
                )
                counts["retrying" if attempt + 1 < 5 else "failed"] += 1

        return counts

    def _handle_failure(
        self,
        delivery_id: int,
        current_attempt: int,
        *,
        http_status: Optional[int] = None,
        error: str = "",
    ) -> None:
        """Handle a failed delivery attempt with exponential backoff."""
        next_attempt = current_attempt + 1
        if next_attempt >= 5:
            self.db.update_webhook_delivery(
                delivery_id,
                status="failed",
                http_status=http_status,
                last_error=error,
                increment_attempt=True,
            )
            logger.warning(
                "Webhook delivery #%d permanently failed after %d attempts: %s",
                delivery_id, next_attempt, error,
            )
        else:
            delay_idx = min(next_attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)
            delay = RETRY_DELAYS_SECONDS[delay_idx]
            next_retry = datetime.now(timezone.utc).isoformat()
            # Compute next_retry_at by adding delay
            from datetime import timedelta

            next_retry_dt = datetime.now(timezone.utc) + timedelta(seconds=delay)
            self.db.update_webhook_delivery(
                delivery_id,
                status="retrying",
                http_status=http_status,
                last_error=error,
                next_retry_at=next_retry_dt.isoformat(),
                increment_attempt=True,
            )
            logger.debug(
                "Webhook delivery #%d retry %d in %ds",
                delivery_id, next_attempt, delay,
            )

    # ── Test fire ─────────────────────────────────────────────────────

    def test_fire(self, webhook_id: int) -> dict:
        """Fire a test event to verify webhook endpoint is reachable.

        Returns delivery result dict with status and response info.
        """
        wh = self.db.get_webhook(webhook_id)
        if wh is None:
            return {"error": "Webhook not found"}

        test_payload = {
            "event_type": "test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {"message": "JennMesh webhook test fire"},
            "source": "jenn-mesh",
        }
        payload_bytes = json.dumps(test_payload).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "JennMesh-Webhook/1.0",
        }
        secret = wh.get("secret", "")
        if secret:
            headers["X-JennMesh-Signature"] = _sign_payload(secret, payload_bytes)

        try:
            import httpx

            with httpx.Client(timeout=10.0) as client:
                resp = client.post(wh["url"], content=payload_bytes, headers=headers)
            return {
                "status": "success" if 200 <= resp.status_code < 300 else "error",
                "http_status": resp.status_code,
                "url": wh["url"],
            }
        except ImportError:
            return {"error": "httpx not installed"}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "url": wh["url"]}


# ── Async delivery loop (started by lifespan) ────────────────────────


async def webhook_delivery_loop_task(manager: WebhookManager) -> None:
    """Background coroutine — processes pending webhook deliveries.

    Same pattern as watchdog_loop_task and retry_loop_task.
    """
    logger.info("Webhook delivery loop started (sleep=%ds)", DELIVERY_LOOP_SLEEP_SECONDS)
    while True:
        try:
            counts = await asyncio.to_thread(manager.process_pending_deliveries)
            total = sum(counts.values())
            if total:
                logger.info("Webhook deliveries: %s", counts)
        except Exception:
            logger.exception("Webhook delivery loop error")
        await asyncio.sleep(DELIVERY_LOOP_SLEEP_SECONDS)
