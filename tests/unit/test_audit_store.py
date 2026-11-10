"""Synchronous refund audit writes (ADR 0011 decision C) over doubles; Postgres: integration."""

from datetime import datetime
from typing import Any

import psycopg
import pytest
from chat_common.audit import AuditLog
from worker import approval
from worker import audit_store as audit_store_module
from worker.audit_store import AuditStore
from worker.consumer import TransientDependencyError


class _Conn:
    """AsyncConnection double: records each statement."""

    def __init__(self, executed: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._executed = executed

    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._executed.append((sql, params))


class _Lease:
    """Lease double: records script runs instead of touching Redis."""

    def __init__(self) -> None:
        self.runs = 0

    async def run(self, *_: Any) -> int:
        self.runs += 1
        return 1


def _connect_to(executed: list[tuple[str, tuple[Any, ...]]]):
    async def connect(*_: Any, **__: Any) -> _Conn:
        return _Conn(executed)

    return connect


def test_entry_carries_its_key_only_when_given() -> None:
    keyed = AuditLog.entry("refund_proposed", actor="a", thread_id="t", key="refund_proposed:m")
    assert keyed["event_key"] == "refund_proposed:m"
    assert "event_key" not in AuditLog.entry("chat_accepted", actor="a", thread_id="t")


def test_an_empty_url_is_refused() -> None:
    with pytest.raises(ValueError, match="AUDIT_DATABASE_URL is required"):
        AuditStore("")


async def test_write_inserts_the_entry_field_for_field(monkeypatch) -> None:
    executed: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setattr(
        audit_store_module.psycopg.AsyncConnection, "connect", _connect_to(executed)
    )
    entry = AuditLog.entry("refund_applied", actor="alice", thread_id="t-1", key="k-1", ref="m")
    await AuditStore("postgresql://w@db/audit").write(entry)
    [(sql, row)] = executed
    assert "ON CONFLICT DO NOTHING" in sql
    assert row[:5] == ("k-1", datetime.fromisoformat(entry["ts"]), "refund_applied", "alice", "t-1")
    assert row[5].obj == {"ref": "m"}


async def test_an_unreachable_database_is_a_dependency_fault(monkeypatch) -> None:
    """Retried, then dead-lettered as dependency_unavailable; never a silent success."""

    async def refused(*_: Any, **__: Any) -> None:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(audit_store_module.psycopg.AsyncConnection, "connect", refused)
    entry = AuditLog.entry("refund_proposed", actor="a", thread_id="t", key="k")
    with pytest.raises(TransientDependencyError, match="OperationalError"):
        await AuditStore("postgresql://w@db/audit").write(entry)


async def test_a_refund_event_without_a_store_fails_before_redis_changes() -> None:
    """A wiring mistake is a loud defect, not a refund recorded only in Redis."""
    lease = _Lease()
    audit = AuditLog.entry("refund_rejected", actor="a", thread_id="t", key="k")
    with pytest.raises(RuntimeError, match="need an AuditStore"):
        await approval.mark(None, lease, audit=audit)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="need an AuditStore"):
        await approval.apply_refund(
            None, thread_id="t", ref_message_id="m", user_id="a", lease=lease, store=None
        )  # type: ignore[arg-type]
    assert lease.runs == 0


async def test_marks_without_an_audit_entry_need_no_store() -> None:
    lease = _Lease()
    await approval.mark(None, lease, pending="pending_approval:t:m")  # type: ignore[arg-type]
    assert lease.runs == 1


async def test_the_entry_reaches_the_store_after_the_redis_script() -> None:
    order: list[str] = []

    class _Store:
        async def write(self, entry: dict[str, str]) -> None:
            order.append(f"store:{entry['event_key']}")

    class _OrderedLease(_Lease):
        async def run(self, *_: Any) -> int:
            order.append("redis")
            return 0  # already recorded: the store is still written, so a retry completes it

    recorded = await approval.apply_refund(
        None,  # type: ignore[arg-type]
        thread_id="t",
        ref_message_id="m",
        user_id="a",
        lease=_OrderedLease(),  # type: ignore[arg-type]
        store=_Store(),  # type: ignore[arg-type]
    )
    assert recorded is False
    assert order == ["redis", "store:refund_applied:m"]
