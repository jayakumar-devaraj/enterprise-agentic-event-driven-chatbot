"""Prometheus counters for the gateway and worker, read from the default registry."""

from uuid import uuid4

import httpx
from fastapi import FastAPI
from gateway.metrics import MetricsMiddleware
from prometheus_client import REGISTRY
from test_gateway_chat import ALICE, body, h  # noqa: F401  (h is a fixture)
from test_worker_consumer import FakeMsg, _env, _ok, _worker
from worker.consumer import TransientDependencyError


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
