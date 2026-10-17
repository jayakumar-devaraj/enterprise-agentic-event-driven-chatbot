"""Trace context crosses the envelope (ADR 0003); both services land in one Jaeger trace."""

import asyncio
import os
import uuid

import httpx
import pytest
from chat_common.tracing import context_from_traceparent, current_traceparent
from opentelemetry import context as otel_context
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

pytestmark = pytest.mark.integration
OTLP = os.getenv("IT_OTLP_ENDPOINT", "http://127.0.0.1:14317")
JAEGER = os.getenv("IT_JAEGER_URL", "http://127.0.0.1:16686")


def _provider(service: str) -> TracerProvider:
    """A provider for one service, exporting synchronously to Jaeger's OTLP gRPC port."""
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=OTLP, insecure=True)))
    return provider


async def test_gateway_and_worker_spans_share_one_trace() -> None:
    """Gateway span → traceparent string (as in Envelope.trace_id) → worker child span → Jaeger."""
    suffix = uuid.uuid4().hex[:6]
    gw, wk = _provider(f"it-gateway-{suffix}"), _provider(f"it-worker-{suffix}")

    with gw.get_tracer("gateway").start_as_current_span("POST /chat") as gw_span:
        traceparent = current_traceparent()
        trace_id = format(gw_span.get_span_context().trace_id, "032x")
    assert traceparent.startswith("00-") and trace_id in traceparent

    token = otel_context.attach(otel_context.Context())  # nothing leaks from the gateway side
    try:
        parent = context_from_traceparent(traceparent)
        with wk.get_tracer("worker").start_as_current_span("worker.process", context=parent):
            pass
    finally:
        otel_context.detach(token)
    gw.shutdown()
    wk.shutdown()

    services: set[str] = set()
    async with httpx.AsyncClient(timeout=10) as client:
        async with asyncio.timeout(30):
            while len(services) < 2:
                r = await client.get(f"{JAEGER}/api/traces/{trace_id}")
                if r.status_code == 200 and r.json().get("data"):
                    processes = r.json()["data"][0]["processes"].values()
                    services = {p["serviceName"] for p in processes}
                if len(services) < 2:
                    await asyncio.sleep(1)
    assert services == {f"it-gateway-{suffix}", f"it-worker-{suffix}"}
