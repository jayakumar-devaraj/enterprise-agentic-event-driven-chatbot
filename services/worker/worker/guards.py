"""Output guardrail L5: scrub model output per sentence chunk and block system-prompt echoes."""

import re
from collections.abc import AsyncIterator, Awaitable, Callable

from worker.llm import SYSTEM_PROMPTS

# A sentence ends at punctuation followed by whitespace, or at a newline: a dot inside "jane.doe",
# "212.555" or "192.168" is not an end, so a streamed entity is never flushed in fragments. The
# chunk is cut after the last such end; text after it waits for more deltas.
_BOUNDARY = re.compile(r"[.!?]\s|\n")
_MAX_CHUNK = 200
_HARD_MAX = 1000
# A forced cut falls only between two complete lowercase words, which no supported entity (digits,
# capitalised names and places, emails, IPs) straddles.
_SAFE_CUT = re.compile(r"(?<=[a-z]) (?=[a-z]+[\s,;:])")
_MAX_OPEN_ECHO = 2000  # keep buffering while an echoed delimiter block is still open
# Small models sometimes repeat the delimited prompt fields; drop them (and stray tags) from output.
_ECHO_BLOCK = re.compile(r"<(user_message|history)>.*?</\1>", re.DOTALL)
_STRAY_TAG = re.compile(r"</?(user_message|history)>")
_OPEN_ECHO = re.compile(r"<(user_message|history)>(?![\s\S]*</\1>)")
_ECHO_PROBES = tuple(p[:40] for p in SYSTEM_PROMPTS.values())
REDACTED = "[redacted]"


def _forced_cut(buffer: str) -> int:
    """Where to cut an over-long chunk: the last safe space; 0 means keep buffering.

    -1 means the buffer reached _HARD_MAX with no safe space. Its end may be the first half of an
    entity whose second half has not arrived, and a half is not recognised, so nothing in it can be
    flushed safely.
    """
    cuts = [m.start() for m in _SAFE_CUT.finditer(buffer)]
    if cuts:
        return cuts[-1]
    return -1 if len(buffer) >= _HARD_MAX else 0


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
        chunk = _STRAY_TAG.sub("", _ECHO_BLOCK.sub("", chunk))
        clean = await scrub(chunk) if chunk.strip() else ""
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
        if _OPEN_ECHO.search(buffer) and len(buffer) < _MAX_OPEN_ECHO:
            continue
        buffer = _ECHO_BLOCK.sub("", buffer)  # never cut inside an echoed block
        ends = [m.end() for m in _BOUNDARY.finditer(buffer)]
        if ends:
            cut = ends[-1]
        elif len(buffer) >= _MAX_CHUNK:
            cut = _forced_cut(buffer)
        else:
            continue
        if cut < 0:  # fail closed: redact the rest rather than flush half an entity
            yield REDACTED
            return
        if cut:
            async for token in drain(buffer[:cut]):
                yield token
            buffer = buffer[cut:]
            if done:
                return
    if buffer.strip():
        async for token in drain(buffer):
            yield token
