"""Structured logger for the app.

Outputs JSON lines on stderr — easy to grep and to ship to a log aggregator.
Falls back to plain text in TTY mode for dev.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("job_id", "row_idx", "phase", "ms", "count", "model"):
            v = getattr(record, key, None)
            if v is not None:
                payload[key] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class PrettyFormatter(logging.Formatter):
    """Compact human-readable output for dev TTY."""
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        extra = ""
        for key in ("job_id", "row_idx", "phase", "ms", "count", "model"):
            v = getattr(record, key, None)
            if v is not None:
                extra += f" {key}={v}"
        return f"{color}{ts} {record.levelname:<5}{self.RESET} {record.name}: {record.getMessage()}{extra}"


def configure(level: str | None = None) -> None:
    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    use_json = os.environ.get("LOG_JSON", "0") == "1" or not sys.stderr.isatty()

    root = logging.getLogger()
    if getattr(root, "_customs_configured", False):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if use_json else PrettyFormatter())
    # Replace any existing handlers to avoid duplicates from uvicorn.
    root.handlers[:] = [handler]
    root.setLevel(level_name)
    # Quiet down a few noisy libraries.
    for name in ("urllib3", "httpx", "asyncio", "matplotlib"):
        logging.getLogger(name).setLevel("WARNING")
    root._customs_configured = True  # type: ignore[attr-defined]


def get(name: str) -> logging.Logger:
    configure()
    return logging.getLogger(name)
