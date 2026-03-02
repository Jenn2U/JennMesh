"""Dashboard middleware — security headers, request logging, rate limiting, CORS.

All custom middleware use raw ASGI (not BaseHTTPMiddleware) for consistency
with the existing ``_NoCacheAPIMiddleware`` in this codebase.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

_SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"x-xss-protection", b"1; mode=block"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
]


class SecurityHeadersMiddleware:
    """Inject standard security headers into every HTTP response."""

    def __init__(self, app: object) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(_SECURITY_HEADERS)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---------------------------------------------------------------------------
# Request logging
# ---------------------------------------------------------------------------

# Paths to skip logging (health checks + static assets)
_SKIP_PREFIXES = ("/health", "/static")


class RequestLoggingMiddleware:
    """Log method, path, status code, and duration for every API request."""

    def __init__(self, app: object) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        start = time.monotonic()
        status_code = 0

        async def capture_status(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            msg = "%s %s %d (%.1fms)"
            args = (method, path, status_code, duration_ms)
            if status_code >= 500:
                logger.error(msg, *args)
            elif status_code >= 400:
                logger.warning(msg, *args)
            else:
                logger.info(msg, *args)


# ---------------------------------------------------------------------------
# Rate limiting  (sliding-window deque, per-IP)
# ---------------------------------------------------------------------------

_DEFAULT_RATE_LIMIT = 120  # requests per window
_DEFAULT_RATE_WINDOW = 60  # seconds


class RateLimitMiddleware:
    """Per-IP sliding-window rate limiter using ``collections.deque``.

    Pattern adapted from JennSentry ``bot/bot_routes.py``.
    """

    def __init__(
        self,
        app: object,
        *,
        rate_limit: int = _DEFAULT_RATE_LIMIT,
        window_seconds: int = _DEFAULT_RATE_WINDOW,
    ) -> None:
        self.app = app
        self.rate_limit = rate_limit
        self.window_seconds = window_seconds
        # Map of client IP → deque of timestamps
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def _client_ip(self, scope: dict) -> str:
        """Extract client IP from ASGI scope."""
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _is_rate_limited(self, ip: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        bucket = self._buckets[ip]

        # Evict expired timestamps
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= self.rate_limit:
            return True

        bucket.append(now)
        return False

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Skip rate limiting on health endpoint (monitoring probes)
        path = scope.get("path", "")
        if path == "/health":
            await self.app(scope, receive, send)
            return

        ip = self._client_ip(scope)
        if self._is_rate_limited(ip):
            logger.warning("Rate limited %s on %s", ip, path)
            await _send_429(send, self.window_seconds)
            return

        await self.app(scope, receive, send)


async def _send_429(send: Callable, retry_after: int) -> None:
    """Send a 429 Too Many Requests ASGI response."""
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"retry-after", str(retry_after).encode()),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b'{"detail":"Too many requests"}',
        }
    )


# ---------------------------------------------------------------------------
# CORS configuration
# ---------------------------------------------------------------------------

_ALLOWED_ORIGINS = [
    "http://localhost:8002",
    "http://127.0.0.1:8002",
    "https://mesh.jenn2u.ai",
]

# LAN origin patterns (CORSMiddleware supports regex via allow_origin_regex)
_LAN_ORIGIN_REGEX = (
    r"https?://(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?"
)


def configure_cors(app: FastAPI) -> None:
    """Add CORS middleware allowing LAN + known origins."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_origin_regex=_LAN_ORIGIN_REGEX,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
        allow_credentials=False,
    )
