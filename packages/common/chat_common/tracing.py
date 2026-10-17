"""OpenTelemetry setup and trace-context propagation through the Kafka envelope (ADR 0003).

The async Kafka producer cannot set per-message headers, so `Envelope.trace_id` carries the W3C
`traceparent` of the gateway span; the worker continues the same trace from it.
"""

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_PROPAGATOR = TraceContextTextMapPropagator()


def setup_tracing(service_name: str, otlp_endpoint: str) -> TracerProvider:
    """Install a global provider exporting OTLP/gRPC to Jaeger; returns it for shutdown."""
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    return provider


def current_traceparent() -> str:
    """W3C traceparent of the active span (empty string when no span is recording)."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier.get("traceparent", "")


def context_from_traceparent(traceparent: str) -> Context:
    """Parent context for a span continuing the trace that produced `traceparent`."""
    return _PROPAGATOR.extract({"traceparent": traceparent})
