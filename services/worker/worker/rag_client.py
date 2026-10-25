"""Client for the RAG service's `POST /v1/retrieve` (SPEC.md §13): one pool, one deadline.

The contract is `docs/contracts/rag-openapi-v1.json`. Retries live in the consumer, not here: a
connection error, a 503 or a 504 raises `TransientDependencyError`, which the consumer retries
while no reply has started. Nothing else is retried.
"""

import asyncio
import logging
import time
import uuid
from typing import Any

import httpx
from chat_common.schemas import TenantId
from pydantic import BaseModel, ConfigDict, TypeAdapter

from worker.consumer import TransientDependencyError

log = logging.getLogger("worker.rag")

_DEADLINE_MARGIN_MS = 500  # the service gives up this much before we do, so its 504 can arrive
_TENANT = TypeAdapter(TenantId)


class RagRejected(Exception):
    """A final answer from the RAG service (or our config) that no retry would change.

    `code` is the `ErrorEvent` code; `detail` is a fixed sentence safe to show the user.
    """

    def __init__(self, code: str, detail: str) -> None:
        """Keep the pair the caller streams as an `ErrorEvent`."""
        super().__init__(code)
        self.code, self.detail = code, detail


class Section(BaseModel):
    """One parent page or tab. `markdown` is raw document text: it never leaves the process."""

    model_config = ConfigDict(extra="ignore")

    page_id: int
    doc_key: str
    page_key: str
    source_name: str
    markdown: str
    score: float
    chunk_ids: list[int]


class Retrieval(BaseModel):
    """Response of `/v1/retrieve`. `sections` is empty whenever `insufficient` is true."""

    model_config = ConfigDict(extra="ignore")

    sections: list[Section]
    insufficient: bool
    queries_run: list[str]
    attempts: int


def parse_tenant_tokens(raw: str) -> dict[str, str]:
    """Parse `token:tenant,...` (the RAG service's own TENANT_TOKENS) into tenant → token.

    A malformed entry, a duplicate tenant or a token the service could not parse fails startup.
    """
    tokens: dict[str, str] = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        token, sep, tenant = entry.partition(":")
        if not sep or not token or not tenant or any(c.isspace() for c in token):
            raise ValueError("RAG_TENANT_TOKENS entries must look like token:tenant")
        tenant = _TENANT.validate_python(tenant)
        if tenant in tokens:
            raise ValueError(f"RAG_TENANT_TOKENS lists tenant {tenant!r} twice")
        tokens[tenant] = token
    return tokens


def _trace_id(traceparent: str) -> str:
    """The 32-hex trace id of a W3C traceparent, else a fresh one (safe as a request id)."""
    parts = traceparent.split("-")
    return parts[1] if len(parts) == 4 and len(parts[1]) == 32 else uuid.uuid4().hex


class RagClient:
    """Asks the RAG service for the sections that answer a question, as one tenant."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float,
        tenant_tokens: dict[str, str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """One shared connection pool; the only timeout is the overall deadline in `retrieve`."""
        self._timeout_s = timeout_s
        self._tokens = tenant_tokens
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(None),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            transport=transport,
        )

    async def aclose(self) -> None:
        """Release the pool."""
        await self._http.aclose()

    async def retrieve(self, tenant_id: str, question: str, *, traceparent: str = "") -> Retrieval:
        """Run the call under one deadline. `question` must already be scrubbed."""
        token = self._tokens.get(tenant_id)
        if token is None:
            raise RagRejected(
                "rag_tenant_unknown", "Document search is not set up for your account."
            )
        request_id = _trace_id(traceparent)
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Request-ID": request_id,
            "X-Timeout-Ms": str(max(1, int(self._timeout_s * 1000) - _DEADLINE_MARGIN_MS)),
        }
        if traceparent:
            headers["traceparent"] = traceparent
        started = time.perf_counter()
        status: Any = None
        try:
            async with asyncio.timeout(self._timeout_s):
                response = await self._http.post(
                    "/v1/retrieve", json={"question": question}, headers=headers
                )
            status = response.status_code
        except (httpx.TransportError, TimeoutError) as exc:
            raise TransientDependencyError(f"rag: {type(exc).__name__}") from exc
        finally:
            log.info(
                "rag call",
                extra={
                    "tenant_id": tenant_id,
                    "request_id": request_id,
                    "status": status,
                    "ms": round((time.perf_counter() - started) * 1000),
                },
            )
        return self._interpret(response)

    @staticmethod
    def _interpret(response: httpx.Response) -> Retrieval:
        """Map a response to a `Retrieval`, a retryable fault or a final rejection."""
        code = response.status_code
        if code in (503, 504):
            raise TransientDependencyError(f"rag: HTTP {code}")
        if code == 401:
            raise RagRejected(
                "rag_unauthorized", "Document search is not available for your account."
            )
        if code == 422:
            raise RagRejected(
                "rag_bad_request", "That question could not be used to search documents."
            )
        try:
            if code != 200:
                raise ValueError(f"HTTP {code}")
            return Retrieval.model_validate_json(response.content)
        except ValueError as exc:  # a 500, another status, or a body off-contract (pydantic's too)
            log.error("rag response rejected", extra={"status": code, "error": type(exc).__name__})
            raise RagRejected(
                "rag_error", "Document search failed. Please try again later."
            ) from exc
