"""LLM-level traces in LangFuse Cloud (scrubbed content only); never fails a request."""

import logging
from typing import Any, Protocol

from opentelemetry.sdk.trace import TracerProvider

log = logging.getLogger("worker.observability")


class LLMObserver(Protocol):
    """Records one generation."""

    def record(
        self, *, trace_id: str, intent: str, engine: str, model: str, input: Any, output: str
    ) -> None: ...


class NullObserver:
    """Used when LangFuse keys are not configured."""

    def record(self, **_: Any) -> None:
        """Do nothing."""


class LangfuseObserver:
    """Sends a `generation` observation per reply, linked to the OTel trace id."""

    def __init__(self, public_key: str, secret_key: str, base_url: str) -> None:
        """Isolated tracer provider so infra spans are not exported to LangFuse."""
        from langfuse import Langfuse

        self._client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
            tracer_provider=TracerProvider(),
        )

    def record(
        self, *, trace_id: str, intent: str, engine: str, model: str, input: Any, output: str
    ) -> None:
        """Input/output are already scrubbed by the gateway (L3) and output guard (L5)."""
        try:
            ctx = {"trace_id": trace_id} if len(trace_id) == 32 else None
            obs = self._client.start_observation(
                trace_context=ctx,  # type: ignore[arg-type]
                name=f"respond.{intent}",
                as_type="generation",
                input=input,
                output=output,
                model=model,
                metadata={"engine": engine, "intent": intent},
            )
            obs.end()
        except Exception as exc:  # noqa: BLE001 — observability must never break the chat path
            log.warning("langfuse record failed", extra={"error": str(exc)})

    def flush(self) -> None:
        """Push buffered observations (tests and shutdown)."""
        self._client.flush()
