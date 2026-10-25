"""Raw PII never persists outside the request: logs, Kafka, the DLQ, Redis and traces (SPEC §9).

Needs the full compose stack up as in the README (AUTH_USERS containing demo/demo-pw):
    uv run pytest -m stack -v
Set A goes through `POST /chat`, so the gateway scrubs it before anything leaves the process. Set B
is produced straight to Kafka as a malformed record, as a buggy producer would, so it skips the
gateway's scrubber. Values are unique per run, so a match cannot come from an earlier run. DLQ
records are decoded before searching, because base64 is an encoding, not scrubbing.
"""

import base64
import json
import random
import subprocess
import time
import uuid

import httpx
import pytest
import redis
from confluent_kafka import Consumer, Producer, TopicPartition

pytestmark = pytest.mark.stack
KAFKA, REDIS_URL, JAEGER = "127.0.0.1:19092", "redis://127.0.0.1:16379/0", "http://127.0.0.1:16686"
AUTH = ("demo", "demo-pw")
COMPOSE = ["docker", "compose", "-f", "deploy/compose.yaml"]


def _card() -> str:
    """A Luhn-valid 16-digit card number starting with 4."""
    digits = [4] + [random.randrange(10) for _ in range(14)]
    total = sum(d if i % 2 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits))
    return "".join(map(str, digits)) + str((10 - total % 10) % 10)


def _pii() -> dict[str, str]:
    """Unique values the scrubber recognises."""
    tag, n = uuid.uuid4().hex[:8], random.randrange
    return {
        "email": f"pii-{tag}@example.org",
        "phone": f"212-555-{n(1000, 10000)}",
        "card": _card(),
        "ssn": f"{n(100, 666)}-{n(10, 100)}-{n(1000, 10000)}",
        "ip": f"10.{n(1, 255)}.{n(1, 255)}.{n(1, 255)}",
    }


def _message(pii: dict[str, str]) -> str:
    """A chat message carrying every value."""
    return (
        f"I am checking a charge. Email {pii['email']}, phone {pii['phone']}, card {pii['card']}, "
        f"SSN {pii['ssn']}, and my server is {pii['ip']}."
    )


def _found(text: str, pii: dict[str, str]) -> list[str]:
    """Which raw values occur in `text`."""
    return [kind for kind, value in pii.items() if value in text]


def _topic_text(topic: str) -> list[str]:
    """Every record value in `topic`, read to its end offsets without committing."""
    consumer = Consumer(
        {"bootstrap.servers": KAFKA, "group.id": f"scan-{uuid.uuid4().hex}",
         "enable.auto.commit": False}
    )  # fmt: skip
    values: list[str] = []
    try:
        partitions = consumer.list_topics(topic, timeout=10).topics[topic].partitions
        for p in partitions:
            low, high = consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
            if high <= low:
                continue
            consumer.assign([TopicPartition(topic, p, low)])
            while True:
                msg = consumer.poll(10)
                assert msg is not None and not msg.error(), f"{topic}[{p}] unreadable"
                values.append((msg.value() or b"").decode("utf-8", "replace"))
                if msg.offset() >= high - 1:
                    break
    finally:
        consumer.close()
    return values


def _dlq_text() -> str:
    """The DLQ with every `original_b64` decoded."""
    out = []
    for value in _topic_text("chat-requests.dlq"):
        record = json.loads(value)
        if "original_b64" in record:
            record["original"] = base64.b64decode(record["original_b64"]).decode("utf-8", "replace")
        out.append(json.dumps(record))
    return "\n".join(out)


def _redis_text(r: redis.Redis, thread: str) -> str:
    """Every Redis value about the thread, plus the audit trail."""
    parts = [json.dumps(r.xrange("audit:events"))]
    for key in r.scan_iter(match=f"*{thread}*", count=1000):
        kind = r.type(key)
        if kind == "string":
            parts.append(str(r.get(key)))
        elif kind == "stream":
            parts.append(json.dumps(r.xrange(key)))
        elif kind == "hash":
            parts.append(json.dumps(r.hgetall(key)))
        elif kind == "ReJSON-RL":
            parts.append(json.dumps(r.json().get(key)))
    return "\n".join(parts)


def _trace_text(message_id: str) -> str:
    """The Jaeger trace(s) whose worker span carries `message_id`."""
    now = time.time()
    params = {
        "query.service_name": "worker",
        "query.operation_name": "worker.process",
        "query.start_time_min": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 600)),
        "query.start_time_max": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 60)),
        "query.search_depth": "200",
    }
    found = httpx.get(f"{JAEGER}/api/v3/traces", params=params, timeout=30).json()
    ids = {
        span["traceId"]
        for rs in found.get("result", {}).get("resourceSpans", [])
        for ss in rs["scopeSpans"]
        for span in ss["spans"]
        if any(a["value"].get("stringValue") == message_id for a in span.get("attributes", []))
    }
    assert ids, "no trace for the message"
    return "\n".join(httpx.get(f"{JAEGER}/api/v3/traces/{t}", timeout=30).text for t in ids)


def test_raw_pii_reaches_no_log_topic_store_or_trace() -> None:
    """Send both sets, wait for both outcomes, then search every sink for raw values."""
    via_gateway, malformed = _pii(), _pii()
    port = subprocess.run([*COMPOSE, "port", "gateway", "8000"], capture_output=True, text=True,
                          check=True).stdout.strip()  # fmt: skip
    thread, mid = f"pii-{uuid.uuid4().hex[:8]}", str(uuid.uuid4())
    body = {"message": _message(via_gateway), "thread_id": thread, "message_id": mid}
    with httpx.Client(base_url=f"http://{port}", auth=AUTH, timeout=60) as client:
        accepted = client.post("/chat", json=body)
        assert accepted.status_code == 202, accepted.text
        streamed = client.get(accepted.json()["stream_url"]).text
    assert "event: done" in streamed

    producer = Producer({"bootstrap.servers": KAFKA})
    producer.produce("chat-requests", value=f"not json: {_message(malformed)}".encode(), key=b"x")
    assert producer.flush(30) == 0
    time.sleep(15)  # the worker dead-letters it; loggers and span processors flush

    logs = subprocess.run(
        [*COMPOSE, "logs", "--no-color", "--since", "10m"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout  # fmt: skip
    requests_topic = "\n".join(v for v in _topic_text("chat-requests") if "not json:" not in v)
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        sinks = {
            "SSE stream": streamed,
            "container logs": logs,
            "chat-requests (gateway records)": requests_topic,
            "DLQ (decoded)": _dlq_text(),
            "Redis": _redis_text(r, thread),
            "Jaeger trace": _trace_text(mid),
        }
    finally:
        r.close()
    leaks = {
        sink: {"gateway set": _found(text, via_gateway), "malformed set": _found(text, malformed)}
        for sink, text in sinks.items()
    }
    leaks = {s: {k: v for k, v in sets.items() if v} for s, sets in leaks.items()}
    assert not {s: v for s, v in leaks.items() if v}, json.dumps(leaks, indent=1)
