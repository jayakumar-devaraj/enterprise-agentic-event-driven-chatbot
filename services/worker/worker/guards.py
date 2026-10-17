"""Output guardrail L5: scrub model output per sentence chunk and block system-prompt echoes."""

import re
from collections.abc import AsyncIterator, Awaitable, Callable

from worker.llm import SYSTEM_PROMPTS

_BOUNDARY = re.compile(r"[.!?\n]\s*$")
_MAX_CHUNK = 200
_ECHO_PROBES = tuple(p[:40] for p in SYSTEM_PROMPTS.values())
REDACTED = "[redacted]"


async def guarded_tokens(
    deltas: AsyncIterator[str], scrub: Callable[[str], Awaitable[str]], max_tokens: int
) -> AsyncIterator[str]:
    """Buffer to a sentence boundary, scrub, check for echoes, then yield space-split tokens.

    Nothing unscrubbed is ever yielded, so nothing unscrubbed reaches the stream, checkpoint,
    logs or traces downstream. A system-prompt echo ends the reply with a single REDACTED token.
    """
    emitted = 0
    buffer = ""
    done = False

    async def drain(chunk: str) -> AsyncIterator[str]:
        nonlocal emitted, done
        clean = await scrub(chunk)
        if any(probe in clean for probe in _ECHO_PROBES):
            done = True
            yield REDACTED
            return
        for token in clean.split():
            if emitted >= max_tokens:
                done = True
                return
            emitted += 1
            yield token

    async for delta in deltas:
        buffer += delta
        if _BOUNDARY.search(buffer) or len(buffer) >= _MAX_CHUNK:
            async for token in drain(buffer):
                yield token
            buffer = ""
            if done:
                return
    if buffer.strip():
        async for token in drain(buffer):
            yield token
