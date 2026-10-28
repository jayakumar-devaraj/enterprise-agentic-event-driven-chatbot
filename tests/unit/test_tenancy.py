"""Tenant binding (D4): the tenant comes only from the authenticated username (SPEC.md §3)."""

import json
from uuid import uuid4

import httpx
import pytest
from chat_common.schemas import Envelope
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from fakes import FakeRedis, RecordingProducer
from gateway import auth
from gateway.app import Deps, create_app
from gateway.producer import envelope_bytes

USERS = {
    "alice": auth.hash_password("alice-pw", salt=b"a" * 16),
    "bob": auth.hash_password("bob-pw", salt=b"b" * 16),
}
ALICE, BOB = ("alice", "alice-pw"), ("bob", "bob-pw")


def _body(**extra: str) -> dict[str, str]:
    return {
        "message": "hello",
        "thread_id": f"t-{uuid4().hex[:8]}",
        "message_id": str(uuid4()),
    } | extra


@pytest.fixture
async def client():
    """App where alice belongs to tenant T1 and bob to none; yields (client, producer)."""
    producer = RecordingProducer()

    async def deps() -> Deps:
        return Deps(
            settings=Settings(),
            redis=FakeRedis(),
            producer=producer,
            scrub=get_scrubber().ascrub,
            users=USERS,
            tenants=auth.parse_user_tenants("alice:T1", USERS),
        )

    app = create_app(deps)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            yield c, producer


def test_parse_user_tenants() -> None:
    """Entries are trimmed; empty input means nobody has a tenant."""
    assert auth.parse_user_tenants(" alice:T1 , bob:T-2 ", USERS) == {"alice": "T1", "bob": "T-2"}
    assert auth.parse_user_tenants("", USERS) == {}


@pytest.mark.parametrize(
    "raw",
    [
        "alice",
        "alice:",
        ":T1",
        "alice:T1,alice:T2",
        "mallory:T1",
        "alice:has space",
        "alice:" + "x" * 65,
    ],
)
def test_malformed_user_tenants_fail_startup(raw: str) -> None:
    """Same stance as AUTH_USERS: a typo must not silently leave a user without documents."""
    with pytest.raises(ValueError):
        auth.parse_user_tenants(raw, USERS)


async def test_chat_envelope_carries_the_tenant_of_the_authenticated_user(client) -> None:
    """alice → T1, bob → none."""
    c, producer = client
    assert (await c.post("/chat", json=_body(), auth=ALICE)).status_code == 202
    assert (await c.post("/chat", json=_body(), auth=BOB)).status_code == 202
    assert [e.tenant_id for e in producer.sent] == ["T1", None]


async def test_a_tenant_in_the_request_body_is_rejected(client) -> None:
    """The body model forbids unknown fields, so a client cannot pick a tenant."""
    c, producer = client
    assert (await c.post("/chat", json=_body(tenant_id="T2"), auth=BOB)).status_code == 422
    assert producer.sent == []


def test_wire_bytes_omit_an_unset_tenant_so_older_workers_accept_them() -> None:
    """`extra="forbid"` rejects an unknown field, so only tenant users may carry the new one."""
    env = Envelope(message_id=uuid4(), thread_id="t", user_id="u", text="x", trace_id="x")
    assert "tenant_id" not in json.loads(envelope_bytes(env))
    with_tenant = env.model_copy(update={"tenant_id": "T1"})
    assert json.loads(envelope_bytes(with_tenant))["tenant_id"] == "T1"


def test_an_envelope_written_before_tenancy_still_loads() -> None:
    """A DLQ record or Kafka value from before this change has no `tenant_id`."""
    old = {
        "message_id": str(uuid4()),
        "thread_id": "t",
        "user_id": "u",
        "text": "x",
        "ts": "2026-10-01T00:00:00Z",
        "trace_id": "x",
        "kind": "chat",
        "ref_message_id": None,
        "approved": None,
    }
    assert Envelope.model_validate_json(json.dumps(old)).tenant_id is None


def test_a_tenant_id_must_match_the_id_pattern() -> None:
    """A worker never builds a token lookup from an arbitrary string."""
    with pytest.raises(ValueError):
        Envelope(
            message_id=uuid4(), thread_id="t", user_id="u", text="", trace_id="x", tenant_id="a b"
        )
