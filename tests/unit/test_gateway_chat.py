"""POST /chat status matrix (SPEC.md §2) with Redis and Kafka test doubles; real scrubber."""

import asyncio
import time
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from chat_common.schemas import Envelope
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from fakes import FakeRedis, RecordingProducer
from gateway import auth
from gateway.app import Deps, create_app, enqueue

USERS = {
    "alice": auth.hash_password("alice-pw", salt=b"a" * 16),
    "bob": auth.hash_password("bob-pw", salt=b"b" * 16),
}
ALICE = ("alice", "alice-pw")
BOB = ("bob", "bob-pw")


class Harness:
    """App wired to doubles, plus an ASGI client."""

    def __init__(self) -> None:
        """Fresh doubles per test."""
        self.redis = FakeRedis()
        self.producer = RecordingProducer()
        self.settings = Settings(rate_limit_per_min=3, max_message_chars=50)

    async def deps(self) -> Deps:
        """Deps factory handed to create_app."""
        return Deps(
            settings=self.settings,
            redis=self.redis,
            producer=self.producer,
            scrub=get_scrubber().ascrub,
            users=USERS,
        )


@pytest.fixture
async def h() -> AsyncIterator[tuple[Harness, httpx.AsyncClient]]:
    """Running app (lifespan executed) and client."""
    harness = Harness()
    app = create_app(harness.deps)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield harness, client


def body(thread: str = "t1", message: str = "hello", mid: str | None = None) -> dict[str, str]:
    """A valid ChatRequest body."""
    return {"message": message, "thread_id": thread, "message_id": mid or str(uuid4())}


async def test_accepts_and_produces_scrubbed_envelope(h) -> None:
    """202 with stream URL; Kafka gets scrubbed text keyed by thread; audit records it."""
    harness, client = h
    b = body(message="I am John Smith, mail jane@example.com")
    r = await client.post("/chat", json=b, auth=ALICE)
    assert r.status_code == 202
    assert r.json() == {
        "message_id": b["message_id"],
        "thread_id": "t1",
        "stream_url": f"/stream/t1/{b['message_id']}",
    }
    (env,) = harness.producer.sent
    assert "jane@example.com" not in env.text and "John Smith" not in env.text
    assert "<EMAIL_ADDRESS>" in env.text and env.user_id == "alice"
    assert harness.redis.streams["audit:events"][0][1]["event"] == "chat_accepted"


async def test_repeat_message_id_same_body_single_produce(h) -> None:
    """Idempotent 202: same body, produced once."""
    harness, client = h
    b = body()
    first = await client.post("/chat", json=b, auth=ALICE)
    second = await client.post("/chat", json=b, auth=ALICE)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert len(harness.producer.sent) == 1


async def test_message_id_reused_on_other_thread_is_422(h) -> None:
    """A message_id belongs to one thread."""
    _, client = h
    mid = str(uuid4())
    assert (await client.post("/chat", json=body("t1", mid=mid), auth=ALICE)).status_code == 202
    assert (await client.post("/chat", json=body("t2", mid=mid), auth=ALICE)).status_code == 422


async def test_message_id_reused_for_a_different_message_is_422(h) -> None:
    """A message_id names one request; reusing it for different text is a caller error."""
    harness, client = h
    mid = str(uuid4())
    first = await client.post("/chat", json=body(message="hello", mid=mid), auth=ALICE)
    second = await client.post("/chat", json=body(message="other", mid=mid), auth=ALICE)
    assert (first.status_code, second.status_code) == (202, 422)
    assert len(harness.producer.sent) == 1


async def test_send_interrupted_before_ack_is_not_reported_accepted_on_retry(h) -> None:
    """Cancellation (or a crash) between claiming the id and the broker ack leaves no record.

    The retry must produce it rather than return 202 for work that was never enqueued.
    """
    harness, _ = h
    deps = await harness.deps()
    env = Envelope(message_id=uuid4(), thread_id="t1", user_id="alice", text="hi", trace_id="x")

    async def interrupted(envelope: Envelope) -> None:
        raise asyncio.CancelledError

    harness.producer.send = interrupted
    with pytest.raises(asyncio.CancelledError):
        await enqueue(deps, env, "alice")
    del harness.producer.send

    accepted = await enqueue(deps, env, "alice")
    assert accepted.message_id == env.message_id
    assert harness.producer.sent == [env]


@pytest.mark.parametrize("creds", [None, ("alice", "wrong"), ("mallory", "x")])
async def test_bad_or_missing_credentials_401(h, creds) -> None:
    """No valid Basic credentials ⇒ 401 with a challenge."""
    _, client = h
    r = await client.post("/chat", json=body(), auth=creds)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Basic"


async def test_foreign_thread_403(h) -> None:
    """The first user owns the thread."""
    harness, client = h
    assert (await client.post("/chat", json=body("t9"), auth=ALICE)).status_code == 202
    assert (await client.post("/chat", json=body("t9"), auth=BOB)).status_code == 403
    assert len(harness.producer.sent) == 1


async def test_ownership_outlives_every_expiring_key(h) -> None:
    """After every key with a TTL has expired, the thread still belongs to its first user.

    Checkpoints refresh their TTL on read, so an expiring owner key could lapse while the history
    survives and let another user claim it.
    """
    harness, client = h
    assert (await client.post("/chat", json=body("t8"), auth=ALICE)).status_code == 202
    for key in list(harness.redis.expiry):
        harness.redis.expiry[key] = 0
    assert (await client.post("/chat", json=body("t8"), auth=BOB)).status_code == 403
    assert (await client.post("/chat", json=body("t8"), auth=ALICE)).status_code == 202


async def test_oversize_413_and_invalid_422(h) -> None:
    """Too long ⇒ 413; bad thread_id or missing field ⇒ 422."""
    _, client = h
    assert (await client.post("/chat", json=body(message="x" * 51), auth=ALICE)).status_code == 413
    assert (await client.post("/chat", json=body("bad id!"), auth=ALICE)).status_code == 422
    assert (await client.post("/chat", json={"message": "hi"}, auth=ALICE)).status_code == 422


async def test_rate_limit_429(h) -> None:
    """Fourth message in a minute on one thread with limit 3 ⇒ 429."""
    _, client = h
    codes = [
        (await client.post("/chat", json=body("rl"), auth=ALICE)).status_code for _ in range(4)
    ]
    assert codes == [202, 202, 202, 429]


async def test_kafka_down_503_and_retry_produces(h) -> None:
    """Producer failure ⇒ 503 and the retry with the same body is produced."""
    harness, client = h
    b = body()
    harness.producer.fail = True
    assert (await client.post("/chat", json=b, auth=ALICE)).status_code == 503
    harness.producer.fail = False
    assert (await client.post("/chat", json=b, auth=ALICE)).status_code == 202
    assert len(harness.producer.sent) == 1


async def test_redis_down_503_on_chat_and_readyz_while_healthz_stays_live(h) -> None:
    """Redis outage ⇒ 503 on chat and readyz; healthz is liveness only and stays 200."""
    harness, client = h
    assert (await client.get("/readyz")).status_code == 200
    harness.redis.fail = True
    assert (await client.post("/chat", json=body(), auth=ALICE)).status_code == 503
    assert (await client.get("/readyz")).status_code == 503
    assert (await client.get("/healthz")).status_code == 200


async def test_kafka_down_readyz_503(h) -> None:
    """Broker unreachable ⇒ not ready, even though Redis answers."""
    harness, client = h
    harness.producer.fail = True
    r = await client.get("/readyz")
    assert r.status_code == 503 and r.json() == {"detail": "dependency unavailable"}


def test_parse_users_rejects_malformed() -> None:
    """Malformed AUTH_USERS fails loudly; the placeholder is not a valid entry."""
    with pytest.raises(ValueError):
        auth.parse_users("REPLACE_ME_user:scrypt-hash")
    assert auth.parse_users(f"alice:{USERS['alice']}") == {"alice": USERS["alice"]}


async def test_password_check_does_not_stall_the_event_loop(h) -> None:
    """scrypt (~150 ms) runs off the loop, so other requests keep being served meanwhile."""
    _, client = h
    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)
    r = await client.post("/chat", json=body(), auth=("alice", "wrong"))
    stop.set()
    await task
    assert r.status_code == 401
    assert max(gaps) < 0.08
