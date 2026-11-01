"""OIDC bearer tokens at the gateway (ADR 0016), against a local issuer serving discovery + JWKS."""

import base64
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx
import jwt
import pytest
from chat_common.settings import Settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fakes import FakeRedis, RecordingProducer
from gateway.app import Deps, create_app
from gateway.oidc import OidcVerifier
from test_gateway_chat import body

AUDIENCE = "chatbot-gateway"


class Issuer:
    """A local OIDC issuer: discovery document and a JWKS whose keys a test can rotate."""

    def __init__(self) -> None:
        """Serve on a free port with one RSA key."""
        self.keys = {"k1": rsa.generate_private_key(public_exponent=65537, key_size=2048)}
        self.up = True
        issuer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (http.server API)
                if not issuer.up:
                    self.send_error(503)
                    return
                if self.path == "/.well-known/openid-configuration":
                    payload = {"issuer": issuer.url, "jwks_uri": f"{issuer.url}/jwks"}
                elif self.path == "/jwks":
                    payload = {"keys": [issuer.jwk(kid) for kid in issuer.keys]}
                else:
                    self.send_error(404)
                    return
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                """Quiet."""

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def jwk(self, kid: str) -> dict[str, Any]:
        """Public JWK of key `kid`."""
        key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.keys[kid].public_key()))
        return {**key, "kid": kid, "use": "sig", "alg": "RS256"}

    def token(self, kid: str = "k1", **overrides: Any) -> str:
        """A token for `alice`, valid for five minutes, with any claim overridden or removed."""
        now = int(time.time())
        claims = {"iss": self.url, "aud": AUDIENCE, "sub": "alice", "iat": now, "exp": now + 300}
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.keys[kid], algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def issuer() -> Iterator[Issuer]:
    """A running local issuer."""
    server = Issuer()
    yield server
    server.server.shutdown()


@pytest.fixture
async def client(issuer: Issuer) -> Any:
    """The gateway in OIDC mode, wired to doubles for Redis and Kafka."""
    redis, producer = FakeRedis(), RecordingProducer()

    async def deps() -> Deps:
        from chat_common.scrubber import get_scrubber

        return Deps(
            settings=Settings(),
            redis=redis,
            producer=producer,
            scrub=get_scrubber().ascrub,
            users={},
            oidc=OidcVerifier(issuer.url, AUDIENCE, cooldown_s=0),
        )

    app = create_app(deps)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c, redis


async def _chat(client: httpx.AsyncClient, authorization: str | None) -> httpx.Response:
    headers = {"Authorization": authorization} if authorization else {}
    return await client.post("/chat", json=body(), headers=headers)


async def test_a_valid_token_is_accepted_and_its_subject_owns_the_thread(client, issuer) -> None:
    """202, and the thread is bound to the token's `sub`, exactly as a Basic username was."""
    c, redis = client
    r = await _chat(c, f"Bearer {issuer.token()}")
    assert r.status_code == 202
    assert await redis.get("thread_owner:t1") == "alice"


@pytest.mark.parametrize(
    "case",
    ["missing", "basic", "garbage", "expired", "wrong_audience", "wrong_issuer", "no_subject",
     "other_key", "alg_none", "hs256_with_public_key"],
)  # fmt: skip
async def test_a_bad_or_missing_token_is_401_with_a_bearer_challenge(client, issuer, case) -> None:
    """Every way a token can be wrong is the caller's fault: 401, never 500, nothing produced."""
    c, _ = client
    now = int(time.time())
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = (
        issuer.keys["k1"]
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    claims = {"iss": issuer.url, "aud": AUDIENCE, "sub": "alice", "exp": now + 300}
    tokens = {
        "missing": None,
        "basic": "Basic YWxpY2U6YWxpY2UtcHc=",
        "garbage": "Bearer not-a-jwt",
        "expired": f"Bearer {issuer.token(exp=now - 3600, iat=now - 7200)}",
        "wrong_audience": f"Bearer {issuer.token(aud='someone-else')}",
        "wrong_issuer": f"Bearer {issuer.token(iss='https://evil.example')}",
        "no_subject": f"Bearer {issuer.token(sub=None)}",
        "other_key": "Bearer "
        + jwt.encode(claims, stranger, algorithm="RS256", headers={"kid": "k1"}),
        "alg_none": "Bearer " + jwt.encode(claims, None, algorithm="none", headers={"kid": "k1"}),
        "hs256_with_public_key": f"Bearer {_hs256(claims, public_pem)}",
    }
    r = await _chat(c, tokens[case])
    assert r.status_code == 401, (case, r.text)
    assert r.headers["WWW-Authenticate"].startswith("Bearer")


def _hs256(claims: dict[str, Any], secret: bytes) -> str:
    """The algorithm-confusion forgery: HS256 keyed with the issuer's public key, built by hand
    (PyJWT refuses to sign it)."""

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    signing_input = ".".join(
        b64(json.dumps(part).encode()) for part in ({"alg": "HS256", "kid": "k1"}, claims)
    )
    signature = hmac.digest(secret, signing_input.encode(), hashlib.sha256)
    return f"{signing_input}.{b64(signature)}"


async def test_a_rotated_key_is_picked_up_without_a_restart(client, issuer) -> None:
    """A token signed by a key added after the first fetch is accepted after one JWKS refetch (the
    refetch cooldown, 30 s by default, is 0 here)."""
    c, _ = client
    assert (await _chat(c, f"Bearer {issuer.token()}")).status_code == 202
    issuer.keys["k2"] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    r = await c.post(
        "/chat", json=body(thread="t2"), headers={"Authorization": f"Bearer {issuer.token('k2')}"}
    )
    assert r.status_code == 202


async def test_an_unreachable_issuer_is_503_not_401(client, issuer) -> None:
    """The caller did nothing wrong when the identity provider is down: infrastructure error."""
    c, _ = client
    issuer.up = False
    r = await _chat(c, f"Bearer {issuer.token()}")
    assert r.status_code == 503


def test_settings_refuse_an_issuer_without_audience_or_together_with_basic() -> None:
    """One way in, configured completely, or the process does not start."""
    with pytest.raises(ValueError, match="OIDC_AUDIENCE"):
        Settings(oidc_issuer="https://issuer.example")
    with pytest.raises(ValueError, match="not both"):
        Settings(oidc_issuer="https://issuer.example", oidc_audience="a", auth_users="u:scrypt$a$b")
