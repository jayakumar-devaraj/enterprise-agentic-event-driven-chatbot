"""Prometheus counters for the gateway and worker, read from the default registry."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from gateway.metrics import MetricsMiddleware
from openai import APIConnectionError
from prometheus_client import REGISTRY
from test_gateway_chat import ALICE, body, h  # noqa: F401  (h is a fixture)
from test_worker_consumer import FakeMsg, _env, _ok, _worker
from worker.consumer import TransientDependencyError
from worker.llm import OpenAICompatEngine


def _value(name: str, **labels: str) -> float:
    """Current sample value; 0 before the first increment."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_gateway_counts_by_route_template_and_status(h) -> None:  # noqa: F811
    """Two threads' requests share one series; the raw path never becomes a label."""
    _, client = h
    before = _value("chatbot_gateway_requests_total", method="POST", route="/chat", status="202")
    for thread in ("t1", "t2"):
        assert (await client.post("/chat", json=body(thread=thread), auth=ALICE)).status_code == 202
    after = _value("chatbot_gateway_requests_total", method="POST", route="/chat", status="202")
    assert after - before == 2
    route = "/approve/{thread_id}/{message_id}"
    labels = {"method": "POST", "route": route, "status": "404"}
    before = _value("chatbot_gateway_requests_total", **labels)
    decision = {"message_id": str(uuid4()), "approved": True}
    r = await client.post(f"/approve/t1/{uuid4()}", json=decision, auth=ALICE)
    assert r.status_code == 404
    assert _value("chatbot_gateway_requests_total", **labels) - before == 1
    before = _value("chatbot_gateway_requests_total", method="GET", route="unmatched", status="404")
    assert (await client.get("/no/such/path")).status_code == 404
    after = _value("chatbot_gateway_requests_total", method="GET", route="unmatched", status="404")
    assert after - before == 1


async def test_gateway_counts_an_unhandled_error_as_500() -> None:
    """An exception no handler maps never sends a response through the middleware; still a 500."""
    app = FastAPI()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    app.add_middleware(MetricsMiddleware)
    before = _value("chatbot_gateway_requests_total", method="GET", route="/boom", status="500")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        assert (await client.get("/boom")).status_code == 500
    after = _value("chatbot_gateway_requests_total", method="GET", route="/boom", status="500")
    assert after - before == 1


async def test_worker_counts_each_recorded_outcome() -> None:
    """done, duplicate, malformed and dependency outcomes each land in their own series."""

    async def _down(env, writer) -> None:
        raise TransientDependencyError("qdrant down")

    names = ("done", "duplicate", "dlq_malformed", "dlq_dependency")
    before = {o: _value("chatbot_worker_records_total", outcome=o) for o in names}
    retries = _value("chatbot_worker_retries_total")
    w, *_ = _worker(_ok)
    msg = FakeMsg(_env().model_dump_json().encode())
    await w.handle(msg)
    await w.handle(msg)  # redelivery after the outcome: skipped
    await w.handle(FakeMsg(b"not json"))
    down, *_ = _worker(_down)
    await down.handle(FakeMsg(_env().model_dump_json().encode()))
    after = {o: _value("chatbot_worker_records_total", outcome=o) for o in names}
    assert {o: after[o] - before[o] for o in names} == dict.fromkeys(names, 1)
    assert _value("chatbot_worker_retries_total") - retries == 2  # max_retries=2 in SETTINGS


class _Completions:
    """TEST DOUBLE for `AsyncOpenAI().chat.completions`: streams `deltas` or raises `exc`."""

    def __init__(self, deltas: list[str], exc: Exception | None = None) -> None:
        self.deltas, self.exc = deltas, exc

    async def create(self, **kwargs):
        if self.exc is not None:
            raise self.exc
        if not kwargs["stream"]:
            message = SimpleNamespace(content=" ".join(self.deltas))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        async def chunks():
            for d in self.deltas:
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=d))])

        return chunks()


def _engine(deltas: list[str], exc: Exception | None = None) -> OpenAICompatEngine:
    engine = OpenAICompatEngine("http://model.invalid/v1", "m", max_tokens=16, timeout_s=5)
    engine._client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions(deltas, exc)))
    return engine


def _calls(call: str, outcome: str) -> float:
    return _value("chatbot_worker_llm_calls_total", call=call, outcome=outcome)


async def test_model_calls_are_counted_by_outcome_with_first_token_time() -> None:
    """ok, transient, abandoned (cancelled) and condense calls each land in their own series."""
    first = _value("chatbot_worker_llm_first_token_seconds_count")
    before = {o: _calls("stream", o) for o in ("ok", "transient", "cancelled")}
    condensed = _calls("condense", "ok")

    assert [d async for d in _engine(["a", "b"]).stream("general", "hi", [])] == ["a", "b"]
    down = httpx.Request("POST", "http://model.invalid/v1/chat/completions")
    with pytest.raises(TransientDependencyError):
        [d async for d in _engine([], APIConnectionError(request=down)).stream("general", "hi", [])]
    abandoned = _engine(["a", "b"]).stream("general", "hi", [])
    assert await anext(abandoned) == "a"
    await abandoned.aclose()  # the caller stops reading after the first delta
    assert await _engine(["short"]).condense("hi", []) == "short"

    after = {o: _calls("stream", o) for o in ("ok", "transient", "cancelled")}
    assert {o: after[o] - before[o] for o in before} == {"ok": 1, "transient": 1, "cancelled": 1}
    assert _calls("condense", "ok") - condensed == 1
    assert _value("chatbot_worker_llm_first_token_seconds_count") - first == 2  # ok + abandoned
