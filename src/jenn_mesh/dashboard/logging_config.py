"""Logging configuration for the JennMesh dashboard.

Sets up rotating file + console handlers with plain-text formatting.
Matches JennSentry's logging pattern — NOT JSON structured logging.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Production log directory; falls back to ./logs/ in development
_PROD_LOG_DIR = Path("/var/log/jenn-mesh")
_DEV_LOG_DIR = Path("logs")

# Rotating file limits
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 5


def configure_logging(log_level: str = "INFO") -> None:
    """Configure root logger with console + optional rotating file handler.

    Args:
        log_level: Python log level name (default from ``LOG_LEVEL`` env var or INFO).
    """
    level_name = os.environ.get("LOG_LEVEL", log_level).upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    # Avoid duplicate handlers on repeated calls (e.g. tests)
    if root.handlers:
        return
    root.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Console handler → stderr
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Rotating file handler — production path first, fallback to dev
    log_dir = _PROD_LOG_DIR if _PROD_LOG_DIR.exists() else _DEV_LOG_DIR
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(log_dir / "dashboard.log"),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # Can't write logs to disk (CI, containers, permissions) — console only
        pass

    # Suppress noisy uvicorn access logs at INFO (they duplicate request logging)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
