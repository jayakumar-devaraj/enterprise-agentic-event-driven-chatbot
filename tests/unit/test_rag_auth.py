"""Per-tenant client-credentials tokens (ADR 0011): fetch, cache, expiry, failures, no fallback."""

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest
from chat_common.settings import Settings
from pydantic import ValidationError
from worker import rag_auth
from worker.consumer import TransientDependencyError
from worker.rag_auth import ClientCredentialsTokens, TokenRejected, parse_tenant_clients
from worker.rag_client import RagClient, RagRejected

CLIENTS = parse_tenant_clients("T1:cid-1:sec-1,T2:cid-2:sec:with:colons")


class TokenEndpoint:
    """TEST DOUBLE for the identity provider's token endpoint: records posts, answers by `reply`."""

    def __init__(self, reply=None) -> None:
        self.forms: list[dict[str, str]] = []
        self.reply = reply or (
            lambda n: httpx.Response(200, json={"access_token": f"jwt-{n}", "expires_in": 300})
        )

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)  # let concurrent callers interleave
        self.forms.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        return self.reply(len(self.forms))


def _tokens(endpoint: TokenEndpoint) -> ClientCredentialsTokens:
    return ClientCredentialsTokens(
        "http://idp.test/token", CLIENTS, transport=httpx.MockTransport(endpoint)
    )


def test_parse_tenant_clients_keeps_colons_in_the_secret_and_rejects_bad_entries():
    assert CLIENTS["T2"].client_secret == "sec:with:colons"
    assert parse_tenant_clients("") == {}
    for bad in ("T1", "T1:cid", "T1:cid:", ":cid:sec", "T1:cid:sec,T1:c2:s2", "T1:c id:sec"):
        with pytest.raises(ValueError):
            parse_tenant_clients(bad)


async def test_each_tenant_gets_its_own_client_and_the_token_is_cached():
    idp = TokenEndpoint()
    tokens = _tokens(idp)
    assert await tokens.token("T1") == "jwt-1"
    assert await tokens.token("T1") == "jwt-1"  # cached: no second request
    assert await tokens.token("T2") == "jwt-2"
    assert await tokens.token("unknown") is None  # no client configured, no request
    assert [f["client_id"] for f in idp.forms] == ["cid-1", "cid-2"]
    assert idp.forms[0] == {
        "grant_type": "client_credentials",
        "client_id": "cid-1",
        "client_secret": "sec-1",
        "scope": "rag:query",
    }


async def test_concurrent_callers_for_one_tenant_share_a_single_fetch():
    idp = TokenEndpoint()
    tokens = _tokens(idp)
    got = await asyncio.gather(*(tokens.token("T1") for _ in range(10)))
    assert set(got) == {"jwt-1"} and len(idp.forms) == 1


async def test_a_token_is_refreshed_before_it_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(rag_auth.time, "monotonic", lambda: now[0])
    idp = TokenEndpoint(
        lambda n: httpx.Response(200, json={"access_token": f"jwt-{n}", "expires_in": 100})
    )
    tokens = _tokens(idp)
    assert await tokens.token("T1") == "jwt-1"
    now[0] += 69  # 100 s lifetime less the 30 s margin is 70 s
    assert await tokens.token("T1") == "jwt-1"
    now[0] += 2
    assert await tokens.token("T1") == "jwt-2"


async def test_failures_are_retryable_or_final():
    for status in (500, 503):
        with pytest.raises(TransientDependencyError):
            await _tokens(TokenEndpoint(lambda n, s=status: httpx.Response(s))).token("T1")
    for status in (400, 401):
        with pytest.raises(TokenRejected):
            await _tokens(
                TokenEndpoint(
                    lambda n, s=status: httpx.Response(s, json={"error": "invalid_client"})
                )
            ).token("T1")
    for body in (
        {"expires_in": 300},
        {"access_token": "", "expires_in": 300},
        {"access_token": "x"},
    ):
        with pytest.raises(TokenRejected):
            await _tokens(TokenEndpoint(lambda n, b=body: httpx.Response(200, json=b))).token("T1")

    async def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(TransientDependencyError):
        await _tokens(unreachable).token("T1")


async def test_the_rag_client_sends_the_provider_token_and_never_the_static_one():
    seen = []

    async def rag(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(
            200, json={"sections": [], "insufficient": True, "queries_run": ["q"], "attempts": 1}
        )

    client = RagClient(
        "http://rag.test",
        5,
        {"T1": "static-token"},
        token_provider=_tokens(TokenEndpoint()),
        transport=httpx.MockTransport(rag),
    )
    await client.retrieve("T1", "q")
    assert seen == ["Bearer jwt-1"]


async def test_a_refused_client_is_a_final_unauthorized_and_an_unknown_tenant_is_unset_up():
    refused = _tokens(TokenEndpoint(lambda n: httpx.Response(401)))
    client = RagClient("http://rag.test", 5, {"T1": "static-token"}, token_provider=refused)
    with pytest.raises(RagRejected) as exc:
        await client.retrieve("T1", "q")
    assert exc.value.code == "rag_unauthorized"  # and no static token was tried
    with pytest.raises(RagRejected) as exc:
        await RagClient("http://rag.test", 5, {}, token_provider=_tokens(TokenEndpoint())).retrieve(
            "T9", "q"
        )
    assert exc.value.code == "rag_tenant_unknown"


async def test_a_403_from_the_service_is_the_same_final_answer_as_a_401():
    async def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "token lacks the rag:query scope"})

    client = RagClient("http://rag.test", 5, {"T1": "t"}, transport=httpx.MockTransport(forbidden))
    with pytest.raises(RagRejected) as exc:
        await client.retrieve("T1", "q")
    assert exc.value.code == "rag_unauthorized"


def test_settings_reject_both_credential_kinds_and_clients_without_a_url():
    base = {"auth_users": "u:x"}
    with pytest.raises(ValidationError, match="not both"):
        Settings(
            **base,
            rag_tenant_tokens="a:T1",
            rag_tenant_clients="T1:c:s",
            rag_token_url="http://i/t",
        )
    with pytest.raises(ValidationError, match="RAG_TOKEN_URL"):
        Settings(**base, rag_tenant_clients="T1:c:s")
    Settings(**base, rag_tenant_clients="T1:c:s", rag_token_url="http://i/t")
