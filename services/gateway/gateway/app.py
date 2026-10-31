"""FastAPI gateway: auth, input guardrails L1–L3, idempotent enqueue (SPEC.md §2–§4)."""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any

import redis.asyncio as aioredis
from chat_common.audit import AuditLog
from chat_common.logjson import configure_logging
from chat_common.schemas import (
    ApprovalRequest,
    ChatAccepted,
    ChatRequest,
    Envelope,
    stream_key,
)
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from chat_common.tracing import current_traceparent, setup_tracing
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.sse import EventSourceResponse, ServerSentEvent
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import start_http_server
from redis.exceptions import RedisError

from gateway import auth
from gateway.metrics import MetricsMiddleware
from gateway.producer import DependencyUnavailable, EnvelopeSink, KafkaEnvelopeProducer
from gateway.sse import expired_event, relay

log = logging.getLogger("gateway")
_basic = HTTPBasic(auto_error=False)


@dataclass
class Deps:
    """Process-wide collaborators, injectable for tests."""

    settings: Settings
    redis: Any  # redis.asyncio.Redis (decode_responses=True) or a test double
    producer: EnvelopeSink
    scrub: Callable[[str], Awaitable[str]]
    users: dict[str, str]
    tenants: dict[str, str] = field(default_factory=dict)  # username → document tenant (SPEC §3)
    verify: auth.Verifier = field(init=False)

    def __post_init__(self) -> None:
        """One credential cache per process, over the startup user table."""
        self.verify = auth.Verifier(self.users)

    @property
    def audit(self) -> AuditLog:
        """Audit writer on the same Redis."""
        return AuditLog(self.redis)


def make_redis(settings: Settings) -> Any:
    """Redis client whose socket timeout outlives the SSE relay's XREAD BLOCK window."""
    return aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=settings.stream_block_ms / 1000 + 5,
    )


def _deps(request: Request) -> Deps:
    """Fetch the Deps bundle attached at startup."""
    return request.app.state.deps


async def current_user(
    creds: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
    deps: Annotated[Deps, Depends(_deps)],
) -> str:
    """401 unless the Basic credentials match AUTH_USERS; scrypt runs off the loop, once a pair."""
    if creds is None or not await asyncio.to_thread(deps.verify, creds.username, creds.password):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid credentials", {"WWW-Authenticate": "Basic"}
        )
    return creds.username


async def bind_owner(deps: Deps, thread_id: str, user: str) -> None:
    """First caller owns the thread; anyone else gets 403 (SPEC.md §3).

    The binding never expires: checkpoints refresh their TTL on read, so any finite owner TTL could
    lapse while the thread's history survives and hand it to the next caller.
    """
    key = auth.owner_key(thread_id)
    await deps.redis.set(key, user, nx=True)
    if await deps.redis.get(key) != user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "thread belongs to another user")


async def require_owner(deps: Deps, thread_id: str, user: str) -> None:
    """403 unless `user` already owns `thread_id` (stream and approve endpoints)."""
    if await deps.redis.get(auth.owner_key(thread_id)) != user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "thread belongs to another user")


async def enforce_rate_limit(deps: Deps, thread_id: str) -> None:
    """Fixed one-minute window per thread (guardrail L2)."""
    key = f"ratelimit:{thread_id}:{int(time.time() // 60)}"
    count = await deps.redis.incr(key)
    if count == 1:
        await deps.redis.expire(key, 60)
    if count > deps.settings.rate_limit_per_min:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")


def request_binding(envelope: Envelope) -> str:
    """What a message_id names: its thread plus a digest of the request it carries."""
    content = json.dumps(
        [envelope.kind, envelope.text, str(envelope.ref_message_id), envelope.approved]
    )
    return f"{envelope.thread_id}:{hashlib.sha256(content.encode()).hexdigest()[:32]}"


async def enqueue(deps: Deps, envelope: Envelope, user: str) -> ChatAccepted:
    """Idempotent on message_id; 202 only once the broker has acknowledged the record.

    `accepted:{id}` and `delivered:{id}` both hold the request binding, so a different request
    under the id is a 422 for as long as either key lives (DEDUPE_TTL_S). `delivered:{id}` is set
    only after the ack and the acceptance audit entry. A repeat of a delivered request returns the
    same 202 without producing. A repeat of an undelivered one (earlier send or audit failed, was
    cancelled, or the process died) produces again; a concurrent repeat may produce twice, which
    the worker's dedupe on message_id absorbs (SPEC.md §5).
    """
    accepted = ChatAccepted(
        message_id=envelope.message_id,
        thread_id=envelope.thread_id,
        stream_url=f"/stream/{envelope.thread_id}/{envelope.message_id}",
    )
    ttl = deps.settings.dedupe_ttl_s
    binding = request_binding(envelope)
    reused = HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "message_id already used for a different request"
    )
    # Checked first: the claim is set before the send, so it expires before the delivered marker.
    delivered = await deps.redis.get(f"delivered:{envelope.message_id}")
    if delivered is not None:
        if delivered != binding:
            raise reused
        return accepted
    claim = f"accepted:{envelope.message_id}"
    if not await deps.redis.set(claim, binding, nx=True, ex=ttl):
        if await deps.redis.get(claim) != binding:
            raise reused
    await deps.producer.send(envelope)
    await deps.audit.record(
        f"{envelope.kind}_accepted",
        actor=user,
        thread_id=envelope.thread_id,
        message_id=str(envelope.message_id),
    )
    await deps.redis.set(f"delivered:{envelope.message_id}", binding, ex=ttl)
    return accepted


def create_app(deps_factory: Callable[[], Awaitable[Deps]] | None = None) -> FastAPI:
    """Build the app; production wiring unless a test supplies `deps_factory`."""

    async def _production_deps() -> Deps:
        settings = Settings()
        configure_logging(settings.log_level)
        setup_tracing("gateway", settings.otel_exporter_otlp_endpoint)
        start_http_server(settings.metrics_port)
        scrubber = get_scrubber(settings.spacy_model)
        users = auth.parse_users(settings.auth_users.get_secret_value())
        return Deps(
            settings=settings,
            redis=make_redis(settings),
            producer=KafkaEnvelopeProducer(settings.kafka_bootstrap_servers, settings.kafka_topic),
            scrub=scrubber.ascrub,
            users=users,
            tenants=auth.parse_user_tenants(settings.user_tenants, users),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        deps = await (deps_factory or _production_deps)()
        app.state.deps = deps
        yield
        await deps.producer.close()
        await deps.redis.aclose()

    app = FastAPI(title="chat-gateway", lifespan=lifespan)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=Settings().cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )

    @app.exception_handler(DependencyUnavailable)
    async def _dep_unavailable(_: Request, exc: DependencyUnavailable) -> JSONResponse:
        log.error("dependency unavailable", extra={"error": str(exc)})
        return JSONResponse({"detail": "dependency unavailable"}, status_code=503)

    @app.exception_handler(RedisError)
    async def _redis_unavailable(_: Request, exc: RedisError) -> JSONResponse:
        log.error("redis unavailable", extra={"error": str(exc)})
        return JSONResponse({"detail": "dependency unavailable"}, status_code=503)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness: 200 while the process serves requests; never checks dependencies."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(deps: Annotated[Deps, Depends(_deps)]) -> dict[str, str]:
        """Readiness: Redis answers and the Kafka request topic is reachable, else 503."""
        await deps.redis.ping()
        await deps.producer.ready()
        return {"status": "ready"}

    async def stream_access(
        thread_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        message_id: uuid.UUID,
        user: Annotated[str, Depends(current_user)],
        deps: Annotated[Deps, Depends(_deps)],
    ) -> tuple[str, bool]:
        """Resolve auth, validation, ownership and a missing stream before streaming starts.

        Returns the stream key and whether the stream expired: the message finished (dedupe
        `done`) but its stream is gone. With no stream and no `delivered:` marker the message was
        never accepted (404). Otherwise a missing stream is still to come (queued or running).
        The stream is checked again after `done` is seen: the worker writes it before marking the
        message `done`, so it may have appeared since the first check.
        """
        await require_owner(deps, thread_id, user)
        key = stream_key(thread_id, message_id)
        if await deps.redis.exists(key):
            return key, False
        if await deps.redis.get(f"delivered:{message_id}") is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown message")
        done = await deps.redis.get(f"dedupe:{message_id}") == "done"
        return key, done and not await deps.redis.exists(key)

    @app.get("/stream/{thread_id}/{message_id}", response_class=EventSourceResponse)
    async def stream(
        access: Annotated[tuple[str, bool], Depends(stream_access)],
        deps: Annotated[Deps, Depends(_deps)],
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> AsyncIterator[ServerSentEvent]:
        """Relay the message's events as SSE (SPEC.md §6)."""
        key, expired = access
        if expired:
            yield expired_event()
            return
        async for event in relay(
            deps.redis,
            key,
            last_event_id,
            block_ms=deps.settings.stream_block_ms,
            max_wait_s=deps.settings.stream_ttl_s,
        ):
            yield event

    @app.post("/approve/{thread_id}/{message_id}", status_code=status.HTTP_202_ACCEPTED)
    async def approve(
        thread_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        message_id: uuid.UUID,
        body: ApprovalRequest,
        user: Annotated[str, Depends(current_user)],
        deps: Annotated[Deps, Depends(_deps)],
    ) -> ChatAccepted:
        """Owner's decision on a paused refund; 404 when nothing is awaiting approval.

        A retry of an already-delivered decision reaches `enqueue` even after the worker removed
        the pending key, so it gets the original 202 (or 422 if the request differs).
        """
        await require_owner(deps, thread_id, user)
        if (
            await deps.redis.get(f"pending_approval:{thread_id}:{message_id}") is None
            and await deps.redis.get(f"delivered:{body.message_id}") is None
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no pending approval for this message")
        envelope = Envelope(
            message_id=body.message_id,
            thread_id=thread_id,
            user_id=user,
            text="",
            trace_id=current_traceparent() or uuid.uuid4().hex,
            kind="approval",
            ref_message_id=message_id,
            approved=body.approved,
        )
        return await enqueue(deps, envelope, user)

    @app.post("/chat", status_code=status.HTTP_202_ACCEPTED)
    async def chat(
        body: ChatRequest,
        user: Annotated[str, Depends(current_user)],
        deps: Annotated[Deps, Depends(_deps)],
    ) -> ChatAccepted:
        """Validate, bind ownership, rate-limit, scrub, enqueue; 202 with the stream URL."""
        if len(body.message) > deps.settings.max_message_chars:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "message too long")
        await bind_owner(deps, body.thread_id, user)
        await enforce_rate_limit(deps, body.thread_id)
        scrubbed = await deps.scrub(body.message)  # L3: raw text never leaves this process
        envelope = Envelope(
            message_id=body.message_id,
            thread_id=body.thread_id,
            user_id=user,
            text=scrubbed,
            trace_id=current_traceparent() or uuid.uuid4().hex,  # ADR 0003: context rides here
            tenant_id=deps.tenants.get(user),  # from the username only, never from the request
        )
        return await enqueue(deps, envelope, user)

    return app
