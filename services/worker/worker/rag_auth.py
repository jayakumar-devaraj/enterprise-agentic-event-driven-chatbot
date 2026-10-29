"""Per-tenant OAuth 2.0 client-credentials tokens for the RAG service (ADR 0011, decision A).

The identity provider fixes each client's `tenant` claim and `rag:query` scope, so a leaked secret
exposes one tenant and the worker cannot name an arbitrary one. A token is fetched on first use,
kept until shortly before it expires, and fetched by one caller at a time per tenant. A failure to
get a token is never answered with a static token.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx
from chat_common.schemas import TenantId
from pydantic import TypeAdapter

from worker.consumer import TransientDependencyError

log = logging.getLogger("worker.rag.auth")

_TENANT = TypeAdapter(TenantId)
_EXPIRY_MARGIN_S = 30  # fetch a new token this long before the old one expires
_FETCH_TIMEOUT_S = 5.0


class TokenRejected(Exception):
    """The identity provider refused this client (a final answer; no retry would change it)."""


@dataclass(frozen=True)
class Client:
    """One tenant's OAuth client."""

    client_id: str
    client_secret: str


def parse_tenant_clients(raw: str) -> dict[str, Client]:
    """Parse `tenant:client_id:client_secret,...` into tenant → Client; a bad entry fails startup.

    The secret is everything after the second colon, so it may itself contain colons.
    """
    clients: dict[str, Client] = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        tenant, _, rest = entry.partition(":")
        client_id, sep, secret = rest.partition(":")
        if not (tenant and client_id and sep and secret) or any(c.isspace() for c in entry):
            raise ValueError("RAG_TENANT_CLIENTS entries must look like tenant:client_id:secret")
        tenant = _TENANT.validate_python(tenant)
        if tenant in clients:
            raise ValueError(f"RAG_TENANT_CLIENTS lists tenant {tenant!r} twice")
        clients[tenant] = Client(client_id, secret)
    return clients


class ClientCredentialsTokens:
    """Access tokens per tenant from the identity provider's token endpoint."""

    def __init__(
        self,
        token_url: str,
        clients: dict[str, Client],
        *,
        scope: str = "rag:query",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """One small pool for the token endpoint; tokens are cached per tenant in memory only."""
        self._url, self._clients, self._scope = token_url, clients, scope
        self._http = httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S, transport=transport)
        self._cache: dict[str, tuple[str, float]] = {}
        self._locks = {tenant: asyncio.Lock() for tenant in clients}

    async def aclose(self) -> None:
        """Release the pool."""
        await self._http.aclose()

    async def token(self, tenant_id: str) -> str | None:
        """A valid access token for `tenant_id`, or None when no client is configured for it."""
        client = self._clients.get(tenant_id)
        if client is None:
            return None
        cached = self._cache.get(tenant_id)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        async with self._locks[tenant_id]:  # one fetch per tenant at a time; the rest reuse it
            cached = self._cache.get(tenant_id)
            if cached and cached[1] > time.monotonic():
                return cached[0]
            token, ttl = await self._fetch(client)
            self._cache[tenant_id] = (token, time.monotonic() + max(ttl - _EXPIRY_MARGIN_S, 0))
            return token

    async def _fetch(self, client: Client) -> tuple[str, float]:
        """POST the client-credentials grant; map failures to retryable or final."""
        try:
            response = await self._http.post(
                self._url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client.client_id,
                    "client_secret": client.client_secret,
                    "scope": self._scope,
                },
            )
        except httpx.TransportError as exc:
            raise TransientDependencyError(f"rag auth: {type(exc).__name__}") from exc
        if response.status_code >= 500:
            raise TransientDependencyError(f"rag auth: HTTP {response.status_code}")
        if response.status_code != 200:
            log.error("rag token refused", extra={"status": response.status_code})
            raise TokenRejected(f"HTTP {response.status_code}")
        try:
            body = response.json()
            token, ttl = body["access_token"], float(body["expires_in"])
        except (ValueError, KeyError, TypeError) as exc:
            log.error("rag token response malformed", extra={"error": type(exc).__name__})
            raise TokenRejected("malformed token response") from exc
        if not isinstance(token, str) or not token:
            raise TokenRejected("malformed token response")
        return token, ttl
