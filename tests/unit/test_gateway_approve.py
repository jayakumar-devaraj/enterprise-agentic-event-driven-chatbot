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
