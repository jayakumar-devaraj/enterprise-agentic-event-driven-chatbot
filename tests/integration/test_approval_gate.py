"""Human approval gate end to end in the worker: real Redis checkpointer, streams and audit."""

import json
import uuid
from datetime import timedelta

import pytest
import redis.asyncio as aioredis
from chat_common.schemas import Envelope
from chat_common.scrubber import get_scrubber
from conftest import REDIS_URL
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from worker import approval
from worker.graph import build_graph, make_processor
from worker.lease import Lease
from worker.llm import MockEngine
from worker.router import Route
from worker.streams import StreamWriter

pytestmark = pytest.mark.integration


class _Billing:
    """Test double router: billing, no injection (Qdrant routing is covered by A2)."""

    async def route(self, text: str) -> Route:
        """Fixed verdict."""
        return Route(intent="billing", score=0.9, injection_score=0.0, injection=False)


async def _process(redis, env: Envelope) -> list[dict]:
    """Run one envelope through the compiled graph; return that message's stream events."""
    lease = Lease(f"dedupe:{env.message_id}")
    await redis.set(lease.key, lease.token, ex=120)
    writer = StreamWriter(redis, env.thread_id, env.message_id, lease=lease, maxlen=100, ttl_s=120)
    async with AsyncRedisSaver.from_conn_string(REDIS_URL, ttl={"default_ttl": 5}) as saver:
        await saver.asetup()
        graph = build_graph(_Billing(), MockEngine(), get_scrubber().ascrub, 256, redis=redis)
        await make_processor(graph.compile(checkpointer=saver), redis=redis)(env, writer)
    return [json.loads(f["data"]) for _, f in await redis.xrange(writer.key)]


async def _refunds_for(redis, ref: str) -> int:
    """Count refund records for one originating message."""
    rows = await redis.xrange(approval.REFUNDS_STREAM)
    return sum(1 for _, f in rows if f["ref_message_id"] == ref)


def _chat(thread: str, text: str) -> Envelope:
    """A chat envelope from alice."""
    return Envelope(
        message_id=uuid.uuid4(), thread_id=thread, user_id="alice", text=text, trace_id="t"
    )


def _decision(thread: str, ref: uuid.UUID, approved: bool) -> Envelope:
    """An approval envelope from alice."""
    return Envelope(
        message_id=uuid.uuid4(),
        thread_id=thread,
        user_id="alice",
        text="",
        trace_id="t",
        kind="approval",
        ref_message_id=ref,
        approved=approved,
    )


async def _audit_events(redis, thread: str) -> list[str]:
    """Audit event names for one thread, oldest first."""
    rows = await redis.xrange("audit:events")
    return [f["event"] for _, f in rows if f["thread_id"] == thread]


async def test_refund_pauses_then_applies_once_on_approval() -> None:
    """No side effect before approval; exactly one refund after it, even if redelivered."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    thread = f"ap-{uuid.uuid4().hex[:8]}"
    ask = _chat(thread, "I want a refund for last month")

    events = await _process(redis, ask)
    assert [e["type"] for e in events] == ["approval_required"]
    assert await _refunds_for(redis, str(ask.message_id)) == 0
    assert await redis.get(approval.pending_key(thread, str(ask.message_id))) == "alice"

    decision = _decision(thread, ask.message_id, approved=True)
    follow = await _process(redis, decision)
    assert " ".join(e["token"] for e in follow if e["type"] == "token") == approval.APPROVED_TEXT
    assert follow[-1]["type"] == "done"
    assert await _refunds_for(redis, str(ask.message_id)) == 1
    assert await redis.get(approval.pending_key(thread, str(ask.message_id))) is None

    again = await _process(redis, _decision(thread, ask.message_id, approved=True))
    assert again[-1] == {
        "type": "error",
        "seq": 0,
        "code": "approval_not_pending",
        "detail": "no refund is awaiting approval",
    }
    assert await _refunds_for(redis, str(ask.message_id)) == 1
    assert await _audit_events(redis, thread) == ["refund_proposed", "refund_applied"]
    await redis.aclose()


async def test_rejected_refund_applies_nothing() -> None:
    """Reject ⇒ no refund record, audited as rejected."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    thread = f"rj-{uuid.uuid4().hex[:8]}"
    ask = _chat(thread, "Please refund my payment")
    await _process(redis, ask)
    follow = await _process(redis, _decision(thread, ask.message_id, approved=False))
    assert " ".join(e["token"] for e in follow if e["type"] == "token") == approval.REJECTED_TEXT
    assert await _refunds_for(redis, str(ask.message_id)) == 0
    assert await _audit_events(redis, thread) == ["refund_proposed", "refund_rejected"]
    await redis.aclose()


async def test_non_refund_billing_message_is_not_gated() -> None:
    """Ordinary billing questions answer directly."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    events = await _process(redis, _chat(f"nb-{uuid.uuid4().hex[:8]}", "Why did my price go up?"))
    assert events[-1]["type"] == "done" and "approval_required" not in {e["type"] for e in events}
    await redis.aclose()


async def test_decision_accepted_after_the_window_is_refused_where_applied() -> None:
    """The gateway checks expiry at acceptance; the worker enforces the same rule when it applies
    the decision, using the acceptance time the envelope carries (SPEC §10)."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    thread = f"ax-{uuid.uuid4().hex[:8]}"
    ask = _chat(thread, "I want a refund for last month")
    await _process(redis, ask)
    late = _decision(thread, ask.message_id, approved=True)
    late = late.model_copy(update={"ts": late.ts + timedelta(seconds=3601)})

    events = await _process(redis, late)
    assert [(e["type"], e.get("code")) for e in events] == [("error", "approval_expired")]
    assert await _refunds_for(redis, str(ask.message_id)) == 0
    assert await redis.get(approval.pending_key(thread, str(ask.message_id))) is None
    assert await _audit_events(redis, thread) == ["refund_proposed", "refund_expired"]
    await redis.aclose()


async def test_decision_from_another_user_is_refused_where_applied() -> None:
    """Only the user who asked can decide, even for an envelope that bypassed the gateway."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    thread = f"au-{uuid.uuid4().hex[:8]}"
    ask = _chat(thread, "I want a refund for last month")
    await _process(redis, ask)
    forged = _decision(thread, ask.message_id, approved=True).model_copy(update={"user_id": "bob"})

    events = await _process(redis, forged)
    assert [(e["type"], e.get("code")) for e in events] == [("error", "approval_forbidden")]
    assert await _refunds_for(redis, str(ask.message_id)) == 0
    assert await redis.get(approval.pending_key(thread, str(ask.message_id))) == "alice"
    follow = await _process(redis, _decision(thread, ask.message_id, approved=True))
    assert follow[-1]["type"] == "done" and await _refunds_for(redis, str(ask.message_id)) == 1
    await redis.aclose()


async def test_competing_decisions_first_wins_second_is_not_pending() -> None:
    """Reject and approve under different message ids, both accepted: the first queued wins."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    thread = f"ac-{uuid.uuid4().hex[:8]}"
    ask = _chat(thread, "Please refund my payment")
    await _process(redis, ask)
    no, yes = (_decision(thread, ask.message_id, approved=a) for a in (False, True))

    first, second = await _process(redis, no), await _process(redis, yes)
    assert " ".join(e["token"] for e in first if e["type"] == "token") == approval.REJECTED_TEXT
    assert [(e["type"], e.get("code")) for e in second] == [("error", "approval_not_pending")]
    assert await _refunds_for(redis, str(ask.message_id)) == 0
    assert await _audit_events(redis, thread) == ["refund_proposed", "refund_rejected"]
    await redis.aclose()
