"""A10: two gateways + two workers; per-thread order, one `done` each, cross-replica streams.

Needs the scaled stack:
    docker compose -f deploy/compose.yaml up -d --wait --scale worker=2 --scale gateway=2
Run: `uv run pytest -m scale -v` (AUTH_USERS must contain demo/demo-pw, as in the README).
"""

import asyncio
import json
import subprocess
import uuid

import httpx
import pytest

pytestmark = pytest.mark.scale
AUTH = ("demo", "demo-pw")
THREADS, TURNS = 6, 3


def _gateway_urls() -> list[str]:
    """Host URLs of every gateway replica, from `docker compose port`."""
    urls = []
    for index in (1, 2):
        out = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "deploy/compose.yaml",
                "port",
                "--index",
                str(index),
                "gateway",
                "8000",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        urls.append(f"http://{out}")
    return urls


async def _events(client: httpx.AsyncClient, url: str) -> list[dict]:
    """Read one SSE stream to its terminal event."""
    events = []
    async with client.stream("GET", url, auth=AUTH, timeout=60) as r:
        assert r.status_code == 200
        async for line in r.aiter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


async def _thread(client: httpx.AsyncClient, gws: list[str], n: int) -> list[list[dict]]:
    """POST all turns back to back, alternating replicas; read each stream on the *other* one."""
    thread = f"a10-{n}-{uuid.uuid4().hex[:6]}"
    mids = []
    for turn in range(TURNS):
        mid = str(uuid.uuid4())
        body = {
            "message": f"turn {turn} of thread {n} about my bill",
            "thread_id": thread,
            "message_id": mid,
        }
        r = await client.post(f"{gws[turn % 2]}/chat", json=body, auth=AUTH)
        assert r.status_code == 202, r.text
        mids.append(mid)
    return [
        await _events(client, f"{gws[(turn + 1) % 2]}/stream/{thread}/{mid}")
        for turn, mid in enumerate(mids)
    ]


async def test_a10_scaled_order_no_duplicates_cross_replica() -> None:
    """Each thread's k-th reply reports turns=k and quotes turn k-1; exactly one done per stream."""
    gws = _gateway_urls()
    assert len(set(gws)) == 2
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(_thread(client, gws, n) for n in range(THREADS)))
    for n, streams in enumerate(results):
        for turn, events in enumerate(streams):
            kinds = [e["type"] for e in events]
            assert kinds.count("done") == 1 and kinds[-1] == "done", (n, turn, kinds)
            assert [e["seq"] for e in events] == list(range(len(events)))
            text = " ".join(e["token"] for e in events if e["type"] == "token")
            assert f"turns={turn + 1}" in text, (n, turn, text)
            previous = f"turn {turn - 1} of thread {n} about my bill"
            expected_last = "-" if turn == 0 else previous[:20]  # mock quotes 20 chars
            assert text.endswith(f"last={expected_last}"), (n, turn, text)
