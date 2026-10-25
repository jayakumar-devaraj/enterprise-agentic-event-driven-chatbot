"""Answer quality with the real model: fixed prompts, deterministic checks, rubric scored by hand.

`data/answer_quality.prompts.jsonl` holds 20 support questions with the key points a good answer
covers, written before any run. This script collects each reply through `POST /chat` on a fresh
thread, records the routed intent from the worker's `route` span in Jaeger, and computes the
deterministic checks. The rubric (`docs/reports/answer-quality-rubric-2026-10-25.md`) is applied
by reading each reply. Every reply is published, so anyone can re-score it.
Run (stack up with MOCK_LLM=false, AUTH_USERS containing demo/demo-pw):
    uv run python tests/eval/answer_quality.py
Writes docs/reports/answer_quality_replies.json.
"""

import asyncio
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "data" / "answer_quality.prompts.jsonl"
OUT = ROOT / "docs" / "reports" / "answer_quality_replies.json"
AUTH = ("demo", "demo-pw")
JAEGER = "http://127.0.0.1:16686"
CLAIM = re.compile(
    r"\b(refund|subscription|plan|account)\w*\b[^.]{0,60}\b(has|have|was|were|is)\s+(been\s+)?"
    r"(approved|processed|issued|completed|cancelled|canceled|paused|updated)\b",
    re.IGNORECASE,
)
URL = re.compile(r"\b(https?://|www\.)\S+|\b\S+\.(com|net|org|io)\b", re.IGNORECASE)
PHONE = re.compile(r"\b\d{3}[-. ]\d{3}[-. ]\d{4}\b|\b1-800-\S+")
ECHO = re.compile(r"<user_message>|<history>|strictly as data from the user", re.IGNORECASE)


def gateway() -> str:
    """Host URL of the first gateway replica."""
    port = subprocess.run(
        ["docker", "compose", "-f", "deploy/compose.yaml", "port", "gateway", "8000"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    return f"http://{port}"


def routed_intent(message_id: str) -> str | None:
    """The `route.intent` attribute of the worker's route span for this message."""
    now = time.time()
    params = {
        "query.service_name": "worker",
        "query.operation_name": "worker.process",
        "query.start_time_min": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 1800)),
        "query.start_time_max": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 60)),
        "query.search_depth": "200",
    }
    found = httpx.get(f"{JAEGER}/api/v3/traces", params=params, timeout=30).json()
    # Search results mix spans of many traces, so match the route span by trace id.
    spans = [
        s
        for rs in found.get("result", {}).get("resourceSpans", [])
        for ss in rs["scopeSpans"]
        for s in ss["spans"]
    ]
    traces = {
        s["traceId"]
        for s in spans
        if any(a["value"].get("stringValue") == message_id for a in s.get("attributes", []))
    }
    if len(traces) != 1:
        return None
    (trace_id,) = traces
    full = httpx.get(f"{JAEGER}/api/v3/traces/{trace_id}", timeout=30).json()
    for rs in full["result"]["resourceSpans"]:
        for ss in rs["scopeSpans"]:
            for s in ss["spans"]:
                for a in s.get("attributes", []):
                    if s["name"] == "route" and a["key"] == "route.intent":
                        return a["value"]["stringValue"]
    return None


async def ask(client: httpx.AsyncClient, prompt: dict) -> dict:
    """One prompt on a fresh thread; the reply and its deterministic checks."""
    mid = str(uuid.uuid4())
    body = {
        "message": prompt["text"],
        "thread_id": f"aq-{uuid.uuid4().hex[:10]}",
        "message_id": mid,
    }
    accepted = await client.post("/chat", json=body)
    tokens: list[str] = []
    terminal = None
    async with client.stream("GET", accepted.json()["stream_url"]) as stream:
        async for line in stream.aiter_lines():
            if line.startswith("data:"):
                event = json.loads(line[5:])
                if event["type"] == "token":
                    tokens.append(event["token"])
                else:
                    terminal = event["type"]
                    break
    reply = " ".join(tokens)
    return {
        **prompt,
        "message_id": mid,
        "terminal": terminal,
        "reply": reply,
        "words": len(reply.split()),
        "claims_action": bool(CLAIM.search(reply)),
        "has_url": bool(URL.search(reply)),
        "has_phone": bool(PHONE.search(reply)),
        "echo": bool(ECHO.search(reply)),
    }


async def main() -> list[dict]:
    """Ask every prompt, two at a time, then attach the routed intent."""
    prompts = [
        json.loads(line) for line in PROMPTS.read_text(encoding="utf-8").splitlines() if line
    ]
    sem = asyncio.Semaphore(2)
    async with httpx.AsyncClient(base_url=gateway(), auth=AUTH, timeout=180) as client:

        async def guarded(p: dict) -> dict:
            async with sem:
                return await ask(client, p)

        results = await asyncio.gather(*(guarded(p) for p in prompts))
    await asyncio.sleep(10)  # let the span processors export
    return [{**r, "routed_intent": routed_intent(r["message_id"])} for r in results]


if __name__ == "__main__":
    results = asyncio.run(main())
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for r in results:
        print(f"{r['id']} {r['routed_intent']} words={r['words']} terminal={r['terminal']}")
