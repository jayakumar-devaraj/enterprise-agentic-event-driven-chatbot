"""OIDC at the gateway against a real issuer: Keycloak's `chatbot` realm (compose profile `oidc`).

Tokens come from Keycloak's token endpoint; the gateway runs in-process with real Redis and Kafka
and validates them against Keycloak's discovery document and JWKS (ADR 0016):
    docker compose -f deploy/compose.yaml --profile oidc up -d --wait kafka redis keycloak
    docker compose -f deploy/compose.yaml --profile oidc up keycloak-init kafka-init
    uv run pytest -m oidc
"""

import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import redis.asyncio as aioredis
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from conftest import KAFKA, REDIS_URL
from gateway.app import Deps, create_app
from gateway.oidc import OidcVerifier
from gateway.producer import KafkaEnvelopeProducer

pytestmark = pytest.mark.oidc

ISSUER = os.getenv("IT_OIDC_ISSUER", "http://localhost:18080/realms/chatbot")
AUDIENCE = "chatbot-gateway"


async def _token(client_id: str, password: str | None = None) -> str:
    """An access token for `demo` from Keycloak's password grant (test clients only)."""
    async with httpx.AsyncClient() as http:
        r = await http.post(
            f"{ISSUER}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": client_id,
                "username": "demo",
                "password": password or os.environ["KEYCLOAK_DEMO_PASSWORD"],
                "scope": "openid",
            },
        )
    r.raise_for_status()
    return r.json()["access_token"]


@pytest.fixture
async def client() -> AsyncIterator[tuple[httpx.AsyncClient, aioredis.Redis]]:
    """The gateway in OIDC mode over the real Redis and Kafka."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)

    async def deps() -> Deps:
        settings = Settings()
        return Deps(
            settings=settings,
            redis=redis,
            producer=KafkaEnvelopeProducer(KAFKA, settings.kafka_topic),
            scrub=get_scrubber().ascrub,
            users={},
            oidc=OidcVerifier(ISSUER, AUDIENCE),
        )

    app = create_app(deps)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c, redis


def _body(thread: str) -> dict[str, str]:
    return {"message": "hello", "thread_id": thread, "message_id": str(uuid.uuid4())}


async def test_a_keycloak_token_for_the_gateway_is_accepted(client) -> None:
    """202; the thread belongs to the token's subject, Keycloak's id for `demo`."""
    c, redis = client
    token = await _token("chatbot-test")
    thread = f"oidc-{uuid.uuid4().hex[:8]}"
    r = await c.post("/chat", json=_body(thread), headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 202, r.text
    owner = await redis.get(f"thread_owner:{thread}")
    assert owner and owner != "demo"  # `sub`, a UUID, not the username


async def test_a_token_issued_for_another_application_is_rejected(client) -> None:
    """Same issuer, same user, but `aud` is not the gateway: 401."""
    c, _ = client
    token = await _token("other-app")
    r = await c.post("/chat", json=_body("oidc-x"), headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_a_wrong_password_gets_no_token_and_basic_is_off(client) -> None:
    """Keycloak refuses the grant; and with OIDC on, a Basic header is 401 too."""
    c, _ = client
    with pytest.raises(httpx.HTTPStatusError):
        await _token("chatbot-test", password="wrong")
    r = await c.post("/chat", json=_body("oidc-y"), auth=("demo", "anything"))
    assert r.status_code == 401
