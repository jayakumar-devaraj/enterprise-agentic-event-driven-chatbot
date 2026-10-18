"""POST /approve status matrix with Redis and Kafka test doubles."""

from uuid import uuid4

import httpx
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from fakes import FakeRedis, RecordingProducer
from gateway import auth
from gateway.app import Deps, create_app

USERS = {
    "alice": auth.hash_password("alice-pw", salt=b"a" * 16),
    "bob": auth.hash_password("bob-pw", salt=b"b" * 16),
}


async def _call(redis: FakeRedis, producer: RecordingProducer, path: str, body: dict, creds):
    """POST through the real app wired to doubles."""

    async def deps() -> Deps:
        return Deps(Settings(), redis, producer, get_scrubber().ascrub, USERS)

    app = create_app(deps)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.post(path, json=body, auth=creds)


async def test_approve_flow_statuses() -> None:
    """404 without a pending approval, 403 for another user, 202 + approval envelope for owner."""
    redis, producer = FakeRedis(), RecordingProducer()
    await redis.set(auth.owner_key("t1"), "alice")
    ref, new = str(uuid4()), str(uuid4())
    body = {"message_id": new, "approved": True}

    r404 = await _call(redis, producer, f"/approve/t1/{ref}", body, ("alice", "alice-pw"))
    await redis.set(f"pending_approval:t1:{ref}", "alice")
    r403 = await _call(redis, producer, f"/approve/t1/{ref}", body, ("bob", "bob-pw"))
    r401 = await _call(redis, producer, f"/approve/t1/{ref}", body, ("alice", "nope"))
    r202 = await _call(redis, producer, f"/approve/t1/{ref}", body, ("alice", "alice-pw"))

    assert (r404.status_code, r403.status_code, r401.status_code) == (404, 403, 401)
    assert r202.status_code == 202 and r202.json()["stream_url"] == f"/stream/t1/{new}"
    (env,) = producer.sent
    assert (env.kind, str(env.ref_message_id), env.approved) == ("approval", ref, True)
    assert redis.streams["audit:events"][-1][1]["event"] == "approval_accepted"


async def test_competing_decisions_under_one_message_id_are_422() -> None:
    """The same approval message_id cannot carry approve and then reject."""
    redis, producer = FakeRedis(), RecordingProducer()
    await redis.set(auth.owner_key("t1"), "alice")
    ref, new = str(uuid4()), str(uuid4())
    await redis.set(f"pending_approval:t1:{ref}", "alice")
    path, alice = f"/approve/t1/{ref}", ("alice", "alice-pw")

    yes = await _call(redis, producer, path, {"message_id": new, "approved": True}, alice)
    again = await _call(redis, producer, path, {"message_id": new, "approved": True}, alice)
    no = await _call(redis, producer, path, {"message_id": new, "approved": False}, alice)

    assert (yes.status_code, again.status_code, no.status_code) == (202, 202, 422)
    assert [e.approved for e in producer.sent] == [True]


async def test_retry_after_the_worker_consumed_the_decision_returns_the_original_202() -> None:
    """The worker removed the pending key after applying it; a lost-reply retry is not 404."""
    redis, producer = FakeRedis(), RecordingProducer()
    await redis.set(auth.owner_key("t1"), "alice")
    ref, new = str(uuid4()), str(uuid4())
    await redis.set(f"pending_approval:t1:{ref}", "alice")
    path, alice = f"/approve/t1/{ref}", ("alice", "alice-pw")

    first = await _call(redis, producer, path, {"message_id": new, "approved": True}, alice)
    await redis.delete(f"pending_approval:t1:{ref}")
    retry = await _call(redis, producer, path, {"message_id": new, "approved": True}, alice)
    flip = await _call(redis, producer, path, {"message_id": new, "approved": False}, alice)
    other = await _call(
        redis, producer, path, {"message_id": str(uuid4()), "approved": True}, alice
    )
    bob = await _call(
        redis, producer, path, {"message_id": new, "approved": True}, ("bob", "bob-pw")
    )

    assert (first.status_code, retry.status_code) == (202, 202)
    assert retry.json() == first.json()
    assert (flip.status_code, other.status_code, bob.status_code) == (422, 404, 403)
    assert len(producer.sent) == 1
