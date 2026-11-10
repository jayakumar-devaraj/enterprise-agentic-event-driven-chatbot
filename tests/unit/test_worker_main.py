"""The worker entry point: startup failures, wiring choices and a clean stop, over doubles."""

import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from chat_common.settings import Settings
from pydantic import ValidationError
from worker import main as worker_main
from worker.lease import LeaseLost
from worker.llm import MockEngine, OpenAICompatEngine
from worker.observability import NullObserver


class _Client:
    """Stands in for every client `run` closes; records its own close under `name`."""

    def __init__(self, name: str, closed: list[str]) -> None:
        self._name, self._closed = name, closed

    async def close(self) -> None:
        self._closed.append(self._name)

    aclose = close


class _Consumer(_Client):
    """AIOConsumer double: each poll finds nothing and then runs `on_poll`."""

    def __init__(self, closed: list[str], on_poll: Callable[[], None]) -> None:
        super().__init__("consumer", closed)
        self._on_poll = on_poll

    async def subscribe(self, topics: list[str]) -> None:
        pass

    async def poll(self, wait_s: float) -> None:
        await asyncio.sleep(0)
        self._on_poll()


class _Saver:
    """FencedRedisSaver double."""

    @staticmethod
    @contextlib.asynccontextmanager
    async def from_conn_string(url: str, ttl: dict[str, Any]) -> AsyncIterator[Any]:
        saver = MagicMock()
        saver.asetup = _noop
        yield saver


async def _noop(*_: Any, **__: Any) -> None:
    pass


async def _checked(*_: Any) -> None:
    """Stands in for `AuditStore.check`; counts startup checks."""
    _checked.calls += 1  # type: ignore[attr-defined]


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[], None]], list[str]]:
    """Replace everything `run` connects to; the Worker itself stays real. Returns closed names."""

    def wire(on_poll: Callable[[], None]) -> list[str]:
        closed: list[str] = []
        monkeypatch.setenv("AUDIT_DATABASE_URL", "postgresql://audit_writer@audit-db/audit")
        monkeypatch.setattr(worker_main.AuditStore, "check", _checked)
        _checked.calls = 0
        for name in ("setup_tracing", "start_http_server", "get_scrubber", "get_embedder"):
            monkeypatch.setattr(worker_main, name, MagicMock())
        for name in ("build_graph", "make_processor"):
            monkeypatch.setattr(worker_main, name, MagicMock())
        monkeypatch.setattr(worker_main, "FencedRedisSaver", _Saver)
        monkeypatch.setattr(
            worker_main.aioredis, "from_url", lambda *_, **__: _Client("redis", closed)
        )
        monkeypatch.setattr(worker_main, "AsyncQdrantClient", lambda **_: _Client("qdrant", closed))
        monkeypatch.setattr(worker_main, "RagClient", lambda *_, **__: _Client("rag", closed))
        monkeypatch.setattr(worker_main, "AIOProducer", lambda _: _Client("dlq", closed))
        monkeypatch.setattr(worker_main, "AIOConsumer", lambda _: _Consumer(closed, on_poll))
        return closed

    return wire


ALL_CLIENTS = {"consumer", "dlq", "rag", "qdrant", "redis"}


def test_main_refuses_a_lease_below_the_formula_before_any_io(monkeypatch) -> None:
    """SPEC §5: a lease that could expire mid-processing stops startup before anything runs."""
    logging_calls, run_calls = MagicMock(), MagicMock()
    monkeypatch.setattr(worker_main, "configure_logging", logging_calls)
    monkeypatch.setattr(worker_main, "run", run_calls)
    monkeypatch.setenv("DEDUPE_LEASE_S", "188")
    with pytest.raises(ValidationError, match="DEDUPE_LEASE_S=188 must exceed"):
        worker_main.main()
    logging_calls.assert_not_called()
    run_calls.assert_not_called()


def test_main_runs_with_valid_settings(monkeypatch) -> None:
    """Valid settings: logging is configured, then `run` receives those settings."""
    seen: list[Settings] = []

    async def run(settings: Settings) -> None:
        seen.append(settings)

    logging_calls = MagicMock()
    monkeypatch.setattr(worker_main, "configure_logging", logging_calls)
    monkeypatch.setattr(worker_main, "run", run)
    worker_main.main()
    assert len(seen) == 1
    logging_calls.assert_called_once_with(seen[0].log_level)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
async def test_each_stop_signal_ends_the_loop_and_closes_every_client(
    wired, monkeypatch, sig
) -> None:
    """The handler `run` registers for the signal ends the consume loop; every client closes."""
    handlers: dict[int, Callable[[], None]] = {}
    monkeypatch.setattr(
        asyncio.get_running_loop(), "add_signal_handler", lambda s, cb: handlers.__setitem__(s, cb)
    )
    closed = wired(lambda: handlers[sig]())
    await asyncio.wait_for(worker_main.run(Settings()), 5)
    assert set(handlers) == {signal.SIGINT, signal.SIGTERM}
    assert set(closed) == ALL_CLIENTS


@pytest.mark.skipif(sys.platform == "win32", reason="Windows event loops have no signal handlers")
async def test_a_real_sigterm_stops_the_worker_cleanly(wired) -> None:
    """The same, with the signal delivered by the operating system."""
    closed = wired(lambda: os.kill(os.getpid(), signal.SIGTERM))
    await asyncio.wait_for(worker_main.run(Settings()), 5)
    assert set(closed) == ALL_CLIENTS


async def test_run_starts_on_an_event_loop_without_signal_handlers(wired, monkeypatch) -> None:
    """Windows raises NotImplementedError when a handler is added; startup must survive it."""

    def unsupported(*_: Any) -> None:
        raise NotImplementedError

    def stop_with_a_failure() -> None:
        raise LeaseLost("end the loop")

    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", unsupported)
    closed = wired(stop_with_a_failure)
    with pytest.raises(LeaseLost):
        await asyncio.wait_for(worker_main.run(Settings()), 5)
    assert set(closed) == ALL_CLIENTS


async def test_a_worker_failure_propagates_and_still_closes_every_client(
    wired, monkeypatch
) -> None:
    """A stop without commit must exit non-zero, so the record is redelivered after restart."""

    def lost() -> None:
        raise LeaseLost("taken over")

    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *_: None)
    closed = wired(lost)
    with pytest.raises(LeaseLost):
        await asyncio.wait_for(worker_main.run(Settings()), 5)
    assert set(closed) == ALL_CLIENTS


async def test_malformed_rag_tokens_fail_before_anything_connects(wired) -> None:
    """A bad RAG_TENANT_TOKENS stops startup first: no tracing, no metrics port, no clients."""
    closed = wired(lambda: None)
    with pytest.raises(ValueError, match="must look like token:tenant"):
        await worker_main.run(Settings(rag_tenant_tokens="no-separator"))
    worker_main.setup_tracing.assert_not_called()
    worker_main.start_http_server.assert_not_called()
    assert closed == []


async def test_a_missing_audit_database_url_fails_before_anything_connects(
    wired, monkeypatch
) -> None:
    """ADR 0011: refund events are written to Postgres, so the worker never starts without it."""
    closed = wired(lambda: None)
    monkeypatch.delenv("AUDIT_DATABASE_URL")
    with pytest.raises(ValueError, match="AUDIT_DATABASE_URL is required"):
        await worker_main.run(Settings())
    worker_main.setup_tracing.assert_not_called()
    assert closed == []


async def test_the_audit_store_is_checked_at_startup_and_reaches_the_refund_steps(
    wired, monkeypatch
) -> None:
    """One startup check, and the same store goes to the graph and the processor."""

    def lost() -> None:
        raise LeaseLost("end the loop")

    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *_: None)
    wired(lost)
    with pytest.raises(LeaseLost):
        await asyncio.wait_for(worker_main.run(Settings()), 5)
    assert _checked.calls == 1  # type: ignore[attr-defined]
    store = worker_main.build_graph.call_args.kwargs["audit_store"]
    assert isinstance(store, worker_main.AuditStore)
    assert worker_main.make_processor.call_args.kwargs["audit_store"] is store


async def test_rag_clients_build_a_token_provider_that_is_closed_on_stop(
    wired, monkeypatch
) -> None:
    """RAG_TENANT_CLIENTS selects client-credentials tokens; their HTTP client closes too."""

    def lost() -> None:
        raise LeaseLost("end the loop")

    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *_: None)
    closed = wired(lost)
    monkeypatch.setattr(
        worker_main, "ClientCredentialsTokens", lambda *_: _Client("tokens", closed)
    )
    settings = Settings(rag_tenant_clients="acme:client:secret", rag_token_url="https://idp/token")
    with pytest.raises(LeaseLost):
        await asyncio.wait_for(worker_main.run(settings), 5)
    assert set(closed) == ALL_CLIENTS | {"tokens"}


def test_engine_is_the_mock_only_when_mock_llm_is_true() -> None:
    """CLAUDE.md §9: the mock is selected only by MOCK_LLM=true."""
    assert isinstance(worker_main.make_engine(Settings(mock_llm=True)), MockEngine)
    assert isinstance(worker_main.make_engine(Settings(mock_llm=False)), OpenAICompatEngine)


@pytest.mark.parametrize("keys", [{}, {"langfuse_public_key": "pk"}, {"langfuse_secret_key": "sk"}])
def test_observer_is_a_no_op_unless_both_keys_are_set(monkeypatch, keys) -> None:
    """Either key alone leaves LLM traces off."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert isinstance(worker_main.make_observer(Settings(**keys)), NullObserver)


def test_observer_is_langfuse_when_both_keys_are_set(monkeypatch) -> None:
    """Both keys reach LangfuseObserver as plain values."""
    observer = MagicMock()
    monkeypatch.setattr(worker_main, "LangfuseObserver", observer)
    settings = Settings(langfuse_public_key="pk", langfuse_secret_key="sk")
    assert worker_main.make_observer(settings) is observer.return_value
    observer.assert_called_once_with("pk", "sk", settings.langfuse_base_url)
