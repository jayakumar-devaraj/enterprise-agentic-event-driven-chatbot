"""Prometheus metrics, served on METRICS_PORT; compose never publishes that port."""

import time
from typing import Any

from prometheus_client import Counter, Histogram

REQUESTS = Counter(
    "chatbot_gateway_requests",
    "HTTP requests by route template and status.",
    ["method", "route", "status"],
)
LATENCY = Histogram(
    "chatbot_gateway_request_seconds",
    "Request start to response headers (an SSE stream counts until it opens).",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


class MetricsMiddleware:
    """Pure ASGI, so a streaming response is not buffered; labels by route template, never by
    raw path, to bound cardinality."""

    def __init__(self, app: Any) -> None:
        """Wrap the ASGI app."""
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """Count once, when the response starts or when an unhandled error becomes a 500."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        started = False

        def record(status: int) -> None:
            route = scope.get("route")
            path = route.path if route is not None else "unmatched"
            REQUESTS.labels(scope["method"], path, str(status)).inc()
            LATENCY.labels(scope["method"], path).observe(time.perf_counter() - start)

        async def _send(message: Any) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                record(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception:
            if not started:
                record(500)  # the outer ServerErrorMiddleware turns it into a 500
            raise
