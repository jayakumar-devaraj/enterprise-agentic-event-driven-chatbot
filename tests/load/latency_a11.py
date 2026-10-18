"""A11: POST latency and time-to-first-token under concurrency, with the mock engine.

Reports numbers only; SLOs are set by the lead after the first measurement.
Run (stack up with MOCK_LLM=true, AUTH_USERS containing demo/demo-pw):
    uv run python tests/load/latency_a11.py --requests 200 --concurrency 20
Writes docs/reports/a11-latency.json.
"""

import argparse
import asyncio
import json
import statistics
import subprocess
import time
import uuid
from pathlib import Path

import httpx

AUTH = ("demo", "demo-pw")
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


async def one(client: httpx.AsyncClient, base: str) -> tuple[float, float]:
    """Return (POST seconds, time-to-first-token seconds) for one fresh thread."""
    body = {
        "message": "Why was I charged twice this month?",
        "thread_id": f"lt-{uuid.uuid4().hex[:10]}",
        "message_id": str(uuid.uuid4()),
    }
    start = time.perf_counter()
    r = await client.post(f"{base}/chat", json=body, auth=AUTH)
    posted = time.perf_counter()
    r.raise_for_status()
    async with client.stream("GET", f"{base}{r.json()['stream_url']}", auth=AUTH) as s:
        async for line in s.aiter_lines():
            if line.startswith("data:") and json.loads(line[5:])["type"] == "token":
                return posted - start, time.perf_counter() - start
    raise RuntimeError("stream ended without a token")


def pct(values: list[float], q: int) -> float:
    """q-th percentile in milliseconds."""
    return round(statistics.quantiles(values, n=100)[q - 1] * 1000, 1)


async def main(requests: int, concurrency: int) -> dict:
    """Run `requests` turns with at most `concurrency` in flight, round-robin over replicas."""
    bases = gateways()
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        timeout=60, limits=httpx.Limits(max_connections=concurrency * 2)
    ) as client:

        async def guarded(i: int) -> tuple[float, float]:
            async with sem:
                return await one(client, bases[i % len(bases)])

        started = time.perf_counter()
        results = await asyncio.gather(*(guarded(i) for i in range(requests)))
        wall = time.perf_counter() - started
    post, ttft = [r[0] for r in results], [r[1] for r in results]
    return {
        "requests": requests,
        "concurrency": concurrency,
        "gateways": len(bases),
        "engine": "mock",
        "wall_s": round(wall, 2),
        "post_ms": {"p50": pct(post, 50), "p95": pct(post, 95)},
        "ttft_ms": {"p50": pct(ttft, 50), "p95": pct(ttft, 95)},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=20)
    args = ap.parse_args()
    report = asyncio.run(main(args.requests, args.concurrency))
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
