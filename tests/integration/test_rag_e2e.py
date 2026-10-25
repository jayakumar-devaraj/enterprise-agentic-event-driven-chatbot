"""Document questions end to end (SPEC.md §13): gateway → Kafka → worker → rag-api → Redis → SSE.

Real: Kafka, Redis, Qdrant, the checkpointer, the gateway app, the worker, the RAG client, the
`rag-api` container and its Postgres, and a model. Needs `docker compose --profile rag up`
(rag-db, rag-api, rag-seed) and Ollama. Endpoints: IT_RAG_URL, IT_RAG_TOKENS, IT_LLM_URL.
"""

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
import pytest
import redis.asyncio as aioredis
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka.aio import AIOConsumer, AIOProducer
from conftest import KAFKA, QDRANT_GRPC_PORT, QDRANT_URL, REDIS_URL
from gateway import auth
from gateway.app import Deps, create_app
from gateway.producer import KafkaEnvelopeProducer
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from qdrant_client import AsyncQdrantClient
from seed.main import seed
from worker.consumer import Worker
from worker.embedding import get_embedder
from worker.graph import NOT_FOUND_TEXT, build_graph, make_processor
from worker.llm import OpenAICompatEngine
from worker.rag_client import RagClient, parse_tenant_tokens
from worker.router import Router

# `rag`, not `integration`: CI runs `-m integration`, which would pick these up without a rag-api.
pytestmark = [pytest.mark.rag, pytest.mark.asyncio(loop_scope="module")]

RAG_URL = os.getenv("IT_RAG_URL", "http://127.0.0.1:18000")
RAG_TOKENS = os.getenv("IT_RAG_TOKENS", "tok-t1:T1,tok-t2:T2")
LLM_URL = os.getenv("IT_LLM_URL", "http://127.0.0.1:11435/v1")
LLM_MODEL = os.getenv("IT_LLM_MODEL", "llama3.2:1b")
QUESTION = "According to the employee handbook, how many days of paid vacation do employees get?"
# Part of the fixture's only page, and not something the vacation question's answer would repeat.
ONLY_IN_SECTIONS = "home-office stipend"
SETTINGS = Settings(
    inference_timeout_s=90,
    max_retries=2,
    retry_backoff_max_s=1,
    dedupe_lease_s=400,
    rag_timeout_s=30,
    rag_tenant_tokens=RAG_TOKENS,
)
USERS = {n: auth.hash_password(f"{n}-pw") for n in ("alice", "bob", "carol")}
TENANTS = auth.parse_user_tenants("alice:T1,bob:T2", USERS)  # carol has none


@dataclass
class Stack:
    """A running gateway and worker pair over one topic."""

    client: httpx.AsyncClient
    redis: object
    saver: AsyncRedisSaver
    dlq_topic: str
    graph: object
    stop: asyncio.Event
    tasks: list = field(default_factory=list)

    async def ask(self, user: str, text: str, thread: str | None = None):
        """POST /chat, wait for the terminal event; return (thread, message_id, events, post_ms)."""
        thread = thread or f"e2e-{uuid.uuid4().hex[:10]}"
        mid = str(uuid.uuid4())
        body = {"message": text, "thread_id": thread, "message_id": mid}
        post_ms = int(time.time() * 1000)
        r = await self.client.post("/chat", json=body, auth=(user, f"{user}-pw"))
        assert r.status_code == 202, r.text
        key = f"tokens:{thread}:{mid}"
        async with asyncio.timeout(150):
            while True:
                rows = await self.redis.xrange(key)
                events = [json.loads(f["data"]) for _, f in rows]
                if events and events[-1]["type"] in ("done", "error", "approval_required"):
                    return (
                        thread,
                        mid,
                        [(i, e) for (i, _), e in zip(rows, events, strict=True)],
                        post_ms,
                    )
                await asyncio.sleep(0.3)


@pytest.fixture(scope="module")
async def collections() -> AsyncIterator[Settings]:
    """The routing collections, seeded from the real data under names no one else uses."""
    sfx = uuid.uuid4().hex[:8]
    s = Settings(
        qdrant_url=QDRANT_URL,
        qdrant_collection=f"it-intents-{sfx}",
        qdrant_injection_collection=f"it-inj-{sfx}",
        qdrant_benign_collection=f"it-ben-{sfx}",
    )
    report = await seed(s)
    s = s.model_copy(
        update={
            "route_threshold": report["route_threshold"],
            "injection_margin": report["injection_margin"],
        }
    )
    yield s
    q = AsyncQdrantClient(url=QDRANT_URL)
    for name in (s.qdrant_collection, s.qdrant_injection_collection, s.qdrant_benign_collection):
        await q.delete_collection(name)
    await q.close()


@asynccontextmanager
async def running_stack(
    collections: Settings, rag_url: str, rag_tokens: str
) -> AsyncIterator[Stack]:
    """Gateway app + worker over fresh topics; the worker's RAG client is the one under test."""
    sfx = uuid.uuid4().hex[:8]
    topic, dlq = f"it-rag-{sfx}", f"it-rag-dlq-{sfx}"
    admin = AdminClient({"bootstrap.servers": KAFKA})
    for fut in admin.create_topics([NewTopic(n, 1, 1) for n in (topic, dlq)]).values():
        fut.result(60)
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    qdrant = AsyncQdrantClient(url=QDRANT_URL, grpc_port=QDRANT_GRPC_PORT, prefer_grpc=True)
    router = Router(
        get_embedder(collections.embed_model),
        qdrant,
        intents_collection=collections.qdrant_collection,
        injection_collection=collections.qdrant_injection_collection,
        benign_collection=collections.qdrant_benign_collection,
        route_threshold=collections.route_threshold,
        injection_margin=collections.injection_margin,
    )
    engine = OpenAICompatEngine(LLM_URL, LLM_MODEL, SETTINGS.llm_max_tokens, 80)
    rag = RagClient(rag_url, SETTINGS.rag_timeout_s, parse_tenant_tokens(rag_tokens))
    scrub = get_scrubber().ascrub
    consumer = AIOConsumer(
        {
            "bootstrap.servers": KAFKA,
            "group.id": f"it-{sfx}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    dlq_producer = AIOProducer({"bootstrap.servers": KAFKA})
    stop = asyncio.Event()

    async def deps() -> Deps:
        return Deps(
            settings=SETTINGS,
            redis=aioredis.from_url(REDIS_URL, decode_responses=True),
            producer=KafkaEnvelopeProducer(KAFKA, topic),
            scrub=scrub,
            users=USERS,
            tenants=TENANTS,
        )

    app = create_app(deps)
    async with (
        AsyncRedisSaver.from_conn_string(REDIS_URL, ttl={"default_ttl": 30}) as saver,
        app.router.lifespan_context(app),
    ):
        await saver.asetup()
        graph = build_graph(
            router,
            engine,
            scrub,
            SETTINGS.llm_max_tokens,
            redis=redis,
            model=LLM_MODEL,
            rag=rag,
            rag_max_tokens=SETTINGS.rag_max_tokens,
            rag_context_chars=SETTINGS.rag_context_chars,
        ).compile(checkpointer=saver)
        worker = Worker(
            SETTINGS,
            consumer=consumer,
            dlq_producer=dlq_producer,
            redis=redis,
            process=make_processor(graph, redis=redis),
            dlq_topic=dlq,
        )
        task = asyncio.create_task(worker.run(topic, stop))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            try:
                yield Stack(client, redis, saver, dlq, graph, stop, [task])
            finally:
                stop.set()
                await task
    await consumer.close()
    await dlq_producer.close()
    await rag.aclose()
    await redis.aclose()
    await qdrant.close()
    admin.delete_topics([topic, dlq])


@pytest.fixture(scope="module")
async def stack(collections) -> AsyncIterator[Stack]:
    async with running_stack(collections, RAG_URL, RAG_TOKENS) as s:
        yield s


def _types(events) -> list[str]:
    return [e["type"] for _, e in events]


def _reply(events) -> str:
    return " ".join(e["token"] for _, e in events if e["type"] == "token")


async def test_t1_question_streams_status_tokens_citations_done(stack: Stack) -> None:
    thread, mid, events, post_ms = await stack.ask("alice", QUESTION)
    types = _types(events)
    assert types[0] == "status" and types[-1] == "done" and types[-2] == "citations", types
    assert "token" in types and set(types[1:-2]) == {"token"}, types
    assert [e["seq"] for _, e in events] == list(range(len(events)))
    assert events[-2][1]["citations"] == [{"doc_key": "handbook.txt", "page_key": "page:1"}]
    first_token_ms = int(next(i for i, e in events if e["type"] == "token").split("-")[0])
    status_ms = int(events[0][0].split("-")[0])
    print(
        f"\nE2E T1: status after {status_ms - post_ms} ms, first token after "
        f"{first_token_ms - post_ms} ms; reply: {_reply(events)!r}"
    )
    # the gateway relays the new event types through SSE unchanged
    sse = await stack.client.get(f"/stream/{thread}/{mid}", auth=("alice", "alice-pw"))
    assert sse.status_code == 200
    kinds = [ln.split(": ", 1)[1] for ln in sse.text.splitlines() if ln.startswith("event: ")]
    assert kinds == types


async def test_t2_asking_the_same_gets_not_found_and_none_of_t1s_text(stack: Stack) -> None:
    thread, mid, events, _ = await stack.ask("bob", QUESTION)
    assert _types(events)[0] == "status" and _types(events)[-1] == "done"
    assert "citations" not in _types(events)
    assert _reply(events) == NOT_FOUND_TEXT
    state = await stack.graph.aget_state({"configurable": {"thread_id": thread}})
    everything = json.dumps([e for _, e in events]) + json.dumps(state.values, default=str)
    # text that exists only in T1's document; bob's own question legitimately says "handbook"
    for t1_text in ("handbook.txt", "Acme", "25 days", "16 weeks", "carry over", ONLY_IN_SECTIONS):
        assert t1_text.lower() not in everything.lower(), t1_text


async def test_a_user_with_no_tenant_gets_a_plain_answer_and_no_search(stack: Stack) -> None:
    _, _, events, _ = await stack.ask("carol", QUESTION)
    types = _types(events)
    assert types[-1] == "done" and "token" in types
    assert "status" not in types and "citations" not in types
    assert "handbook.txt" not in json.dumps([e for _, e in events])


async def test_section_text_is_not_in_the_checkpoint(stack: Stack) -> None:
    thread, _, events, _ = await stack.ask("alice", QUESTION)
    state = await stack.graph.aget_state({"configurable": {"thread_id": thread}})
    assert ONLY_IN_SECTIONS not in _reply(events)  # the premise: the reply did not quote it
    assert ONLY_IN_SECTIONS not in json.dumps(state.values, default=str)
    assert state.values["doc_refs"] and set(state.values["doc_refs"][0]) == {
        "doc_key",
        "page_id",
        "page_key",
        "score",
    }
    keys = [k async for k in stack.redis.scan_iter(match=f"*{thread}*")]
    for key in keys:
        if await stack.redis.type(key) == "string":
            assert ONLY_IN_SECTIONS not in (await stack.redis.get(key) or "")
        elif await stack.redis.type(key) == "ReJSON-RL":
            raw = await stack.redis.execute_command("JSON.GET", key)
            assert ONLY_IN_SECTIONS not in raw


async def test_a_401_from_the_service_is_final_and_not_dead_lettered(collections) -> None:
    async with running_stack(collections, RAG_URL, "wrong-token:T1") as s:
        _, _, events, _ = await s.ask("alice", QUESTION)
        # read while the topic exists; the unreachable-service test below shows this reader works
        assert await _dlq_records(s.dlq_topic, wait_s=8) == []
    assert _types(events) == ["status", "error"] and events[-1][1]["code"] == "rag_unauthorized"


async def test_an_unreachable_service_retries_boundedly_then_dead_letters_the_scrubbed_envelope(
    collections,
) -> None:
    text = f"{QUESTION} My email is jane.doe@example.com"
    async with running_stack(collections, "http://127.0.0.1:9", RAG_TOKENS) as s:
        _, mid, events, _ = await s.ask("alice", text)
        (record,) = await _dlq_records(s.dlq_topic, wait_s=20)
    assert _types(events) == ["status", "error"]
    assert events[-1][1]["code"] == "dependency_unavailable"
    assert record["reason"] == "dependency_unavailable" and record["envelope"]["tenant_id"] == "T1"
    assert "jane.doe@example.com" not in json.dumps(record)
    assert record["envelope"]["message_id"] == mid


async def _dlq_records(topic: str, wait_s: float) -> list[dict]:
    consumer = AIOConsumer(
        {"bootstrap.servers": KAFKA, "group.id": uuid.uuid4().hex, "auto.offset.reset": "earliest"}
    )
    await consumer.subscribe([topic])
    out: list[dict] = []
    deadline = time.monotonic() + wait_s
    try:
        while time.monotonic() < deadline:
            msg = await consumer.poll(1.0)
            if msg is not None and not msg.error():
                out.append(json.loads(msg.value()))
    finally:
        await consumer.close()
    return out
