"""A11: latency under concurrency: POST, queue delay, first token, completion, errors, throughput.

Reports numbers; targets are pre-registered separately (`docs/reports/latency-targets-*.md`).
Run (stack up, AUTH_USERS containing demo/demo-pw, Jaeger on 127.0.0.1:16686):
    uv run python tests/load/latency_a11.py --requests 200 --concurrency 20
    uv run python tests/load/latency_a11.py --requests 20 --concurrency 2 --engine ollama \
        --out docs/reports/latency-ollama.json
    uv run python tests/load/latency_a11.py --requests 200 --rate 15   # open loop, 15 turns/s
With `--rate`, turns arrive with seeded exponential gaps, whatever earlier ones are doing (open
loop); without it, `--concurrency` turns are in flight and each starts when one ends (closed loop).
Queue delay comes from Jaeger: the worker's `worker.process` span start minus the end of the
gateway's `kafka.produce` span start (the record handed to the Kafka client), same trace.
"""

import argparse
import asyncio
import itertools
import json
import os
import random
import statistics
import subprocess
import time
import uuid
from pathlib import Path

import httpx

AUTH = ("demo", "demo-pw")
# A second compose project publishes Jaeger elsewhere (JAEGER_UI_HOST_PORT): set JAEGER_URL.
JAEGER = os.environ.get("JAEGER_URL", "http://127.0.0.1:16686")
REPORT = Path(__file__).resolve().parents[2] / "docs" / "reports" / "a11-latency.json"


def gateways() -> list[str]:
    """Host URLs of all gateway replicas (docker compose port)."""
    urls = []
    for index in range(1, 5):
        res = subprocess.run(
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
        )
        if res.returncode == 0 and res.stdout.strip():
            urls.append(f"http://{res.stdout.strip()}")
    return urls


async def one(client: httpx.AsyncClient, base: str) -> dict:
    """One turn on a fresh thread: POST, first token and `done` times (s), or the error."""
    thread = f"lt-{uuid.uuid4().hex[:10]}"
    body = {"message": "Why was I charged twice this month?", "thread_id": thread,
            "message_id": str(uuid.uuid4())}  # fmt: skip
    out: dict = {"thread": thread, "message_id": body["message_id"]}
    start = time.perf_counter()
    try:
        r = await client.post(f"{base}/chat", json=body, auth=AUTH)
        out["post"] = time.perf_counter() - start
        if r.status_code != 202:
            return {**out, "error": f"POST {r.status_code}"}
        async with client.stream("GET", f"{base}{r.json()['stream_url']}", auth=AUTH) as s:
            async for line in s.aiter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:])
                if event["type"] == "token" and "ttft" not in out:
                    out["ttft"] = time.perf_counter() - start
                elif event["type"] == "done":
                    return {**out, "done": time.perf_counter() - start}
                elif event["type"] != "token":
                    return {**out, "error": f"stream {event['type']} {event.get('code', '')}"}
        return {**out, "error": "stream ended without done"}
    except httpx.HTTPError as exc:
        return {**out, "error": f"{type(exc).__name__}"}


def queue_delays(
    message_ids: set[str], since_s: float, until_s: float | None = None
) -> dict[str, float]:
    """Per message id, seconds from the record reaching the gateway's Kafka client to the worker's
    span start."""
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since_s - 60))
    until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((until_s or time.time()) + 60))
    params = {
        "query.service_name": "worker",
        "query.operation_name": "worker.process",
        "query.start_time_min": since,
        "query.start_time_max": until,
        "query.search_depth": str(len(message_ids) * 2),  # newest first: keep the window tight
    }
    found = httpx.get(f"{JAEGER}/api/v3/traces", params=params, timeout=60).json()
    trace_ids = {
        span["traceId"]: a["value"]["stringValue"]
        for rs in found.get("result", {}).get("resourceSpans", [])
        for ss in rs["scopeSpans"]
        for span in ss["spans"]
        if span["name"] == "worker.process"
        for a in span.get("attributes", [])
        if a["key"] == "message_id" and a["value"].get("stringValue") in message_ids
    }
    delays = {}
    for trace_id, message_id in trace_ids.items():
        full = httpx.get(f"{JAEGER}/api/v3/traces/{trace_id}", timeout=30).json()
        spans = [
            span
            for rs in full["result"]["resourceSpans"]
            for ss in rs["scopeSpans"]
            for span in ss["spans"]
        ]
        handed = [int(s["startTimeUnixNano"]) for s in spans if s["name"] == "kafka.produce"]
        worker = [int(s["startTimeUnixNano"]) for s in spans if s["name"] == "worker.process"]
        if handed and worker:
            delays[message_id] = (min(worker) - max(handed)) / 1e9
    return delays


def pct(values: list[float]) -> dict[str, float] | None:
    """p50/p95/p99 in milliseconds; None if nothing succeeded, so the errors still print."""
    if not values:
        return None
    q = statistics.quantiles(values, n=100, method="inclusive")  # never beyond the observed range
    return {f"p{n}": round(q[n - 1] * 1000, 1) for n in (50, 95, 99)}


async def main(
    requests: int, concurrency: int, engine: str, rate: float | None = None, seed: int = 0
) -> dict:
    """Run `requests` turns round-robin over replicas: at most `concurrency` in flight (closed
    loop), or arriving at `rate` per second with exponential gaps (open loop)."""
    bases = gateways()
    sem = asyncio.Semaphore(concurrency if rate is None else requests)
    gaps = random.Random(seed)
    offsets = (
        list(itertools.accumulate(gaps.expovariate(rate) for _ in range(requests))) if rate else []
    )
    async with httpx.AsyncClient(
        timeout=120, limits=httpx.Limits(max_connections=(concurrency if rate is None else 200) * 2)
    ) as client:

        async def guarded(i: int) -> dict:
            if rate is not None:
                await asyncio.sleep(offsets[i] - (time.perf_counter() - started))
            async with sem:
                return await one(client, bases[i % len(bases)])

        began, started = time.time(), time.perf_counter()
        results = await asyncio.gather(*(guarded(i) for i in range(requests)))
        wall = time.perf_counter() - started
    ok = [r for r in results if "error" not in r]
    errors = [r["error"] for r in results if "error" in r]
    await asyncio.sleep(10)  # let the batch span processors export
    by_message = queue_delays({r["message_id"] for r in ok}, began)
    delays = list(by_message.values())
    ordered = [by_message[r["message_id"]] for r in results if r["message_id"] in by_message]
    return {
        "requests": requests,
        "concurrency": concurrency if rate is None else None,
        "rate_per_s": rate,
        "seed": seed if rate is not None else None,
        "gateways": len(bases),
        "engine": engine,
        "wall_s": round(wall, 2),
        "throughput_turns_per_s": round(len(ok) / wall, 2),
        "errors": len(errors),
        "error_kinds": sorted(set(errors)),
        "post_ms": pct([r["post"] for r in ok]),
        "queue_delay_ms": {**pct(delays), "traced": len(delays)} if len(delays) > 1 else None,
        # In arrival order: a growing backlog shows as a later p95 well above an earlier one.
        "queue_delay_first50_ms": pct(ordered[:50]) if len(ordered) >= 100 else None,
        "queue_delay_last50_ms": pct(ordered[-50:]) if len(ordered) >= 100 else None,
        "empty_replies": sum(1 for r in ok if "ttft" not in r),  # `done` with no token
        "ttft_ms": pct([r["ttft"] for r in ok if "ttft" in r]),
        "completion_ms": pct([r["done"] for r in ok]),
        "samples_s": [{k: v for k, v in r.items() if k != "thread"} for r in results],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--rate", type=float, default=None, help="open loop: turns per second")
    ap.add_argument("--seed", type=int, default=0, help="open loop: arrival schedule seed")
    ap.add_argument("--engine", default="mock")
    ap.add_argument("--out", type=Path, default=REPORT)
    args = ap.parse_args()
    report = asyncio.run(main(args.requests, args.concurrency, args.engine, args.rate, args.seed))
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
