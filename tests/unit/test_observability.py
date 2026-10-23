"""LLM-level tracing failures never fail the chat path (SPEC §7, HLD failure table)."""

import logging

import pytest
from worker.observability import LangfuseObserver


class _Unreachable:
    """Test double for the LangFuse client: every call fails like an unreachable endpoint."""

    def start_observation(self, **_: object) -> None:
        """Fail."""
        raise ConnectionError("langfuse unreachable")


def test_langfuse_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    """`record` returns normally and leaves one warning when the client raises."""
    observer = LangfuseObserver.__new__(LangfuseObserver)  # skip the real client's setup
    observer._client = _Unreachable()
    with caplog.at_level(logging.WARNING, logger="worker.observability"):
        observer.record(
            trace_id="0" * 32, intent="billing", engine="mock", model="m", input={}, output="x"
        )
    assert [r.getMessage() for r in caplog.records] == ["langfuse record failed"]
