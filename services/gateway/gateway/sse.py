"""Redis Stream → SSE relay (SPEC §6): replay from 0 or after Last-Event-ID; close on terminal."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from chat_common.schemas import TERMINAL_TYPES, ErrorEvent
from fastapi.sse import ServerSentEvent

log = logging.getLogger("gateway.sse")


async def relay(
    redis: Any, key: str, last_event_id: str | None, *, block_ms: int, max_wait_s: float
) -> AsyncIterator[ServerSentEvent]:
    """Yield stored events in order; `id:` is the Redis entry id, the payload carries `seq`.

    Reading starts at `0` (late joiners get everything) or strictly after `Last-Event-ID`.
    Stops after a terminal event, or after `max_wait_s` without one. A client disconnect cancels
    the generator; that is normal and is not logged as an error.
    """
    cursor = last_event_id or "0"
    deadline = time.monotonic() + max_wait_s
    try:
        while time.monotonic() < deadline:
            batches = await redis.xread({key: cursor}, block=block_ms, count=100)
            for _stream, entries in batches or []:
                for entry_id, fields in entries:
                    cursor = entry_id
                    payload = fields["data"]
                    kind = json.loads(payload)["type"]
                    yield ServerSentEvent(id=entry_id, event=kind, raw_data=payload)
                    if kind in TERMINAL_TYPES:
                        return
    except asyncio.CancelledError:
        log.info("client disconnected", extra={"stream": key})
        raise


def expired_event() -> ServerSentEvent:
    """Terminal event for a finished message whose stream outlived STREAM_TTL_S (SPEC §6)."""
    event = ErrorEvent(
        seq=0, code="stream_expired", detail="This reply is no longer available to stream."
    )
    return ServerSentEvent(event="error", raw_data=event.model_dump_json())
