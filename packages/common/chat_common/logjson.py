"""Structured JSON logging on the stdlib `logging` module."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

_RESERVED = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line; `extra=` fields (trace_id, thread_id) become top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialise the record, its extras and any exception."""
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Route the root logger to stderr through `JsonFormatter`, replacing existing handlers."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
