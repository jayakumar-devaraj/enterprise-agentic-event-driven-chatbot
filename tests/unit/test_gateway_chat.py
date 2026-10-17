"""POST /chat status matrix (SPEC.md §2) with Redis and Kafka test doubles; real scrubber."""

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from fakes import FakeRedis, RecordingProducer
from gateway import auth
from gateway.app import Deps, create_app

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
    """Producer failure ⇒ 503 and the idempotency key is released so a retry succeeds."""
    harness, client = h
    b = body()
    harness.producer.fail = True
    assert (await client.post("/chat", json=b, auth=ALICE)).status_code == 503
    harness.producer.fail = False
    assert (await client.post("/chat", json=b, auth=ALICE)).status_code == 202
    assert len(harness.producer.sent) == 1


async def test_redis_down_503_and_healthz(h) -> None:
    """Redis outage ⇒ 503 on chat and healthz; healthz is 200 otherwise."""
    harness, client = h
    assert (await client.get("/healthz")).status_code == 200
    harness.redis.fail = True
    assert (await client.post("/chat", json=body(), auth=ALICE)).status_code == 503
    assert (await client.get("/healthz")).status_code == 503


def test_parse_users_rejects_malformed() -> None:
    """Malformed AUTH_USERS fails loudly; the placeholder is not a valid entry."""
    with pytest.raises(ValueError):
        auth.parse_users("REPLACE_ME_user:scrypt-hash")
    assert auth.parse_users(f"alice:{USERS['alice']}") == {"alice": USERS["alice"]}
