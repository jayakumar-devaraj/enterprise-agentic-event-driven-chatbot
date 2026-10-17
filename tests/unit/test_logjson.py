"""JSON log formatter."""

import json
import logging
import sys

from chat_common.logjson import JsonFormatter


def _record(**extra: object) -> logging.LogRecord:
    """Build a record the way `logger.info(..., extra=...)` would."""
    rec = logging.LogRecord("worker", logging.INFO, __file__, 1, "routed %s", ("billing",), None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_line_is_json_with_context_fields() -> None:
    """Each line parses as JSON and carries trace/thread ids as top-level keys."""
    out = json.loads(JsonFormatter().format(_record(trace_id="abc", thread_id="t1")))
    assert out["msg"] == "routed billing"
    assert out["level"] == "INFO" and out["logger"] == "worker"
    assert out["trace_id"] == "abc" and out["thread_id"] == "t1"


def test_exception_is_included() -> None:
    """Exceptions are serialised, not lost."""
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "fail", (), sys.exc_info())
    out = json.loads(JsonFormatter().format(rec))
    assert "RuntimeError: boom" in out["exc"]
