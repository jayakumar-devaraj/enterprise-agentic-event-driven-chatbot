"""A9: one real round trip through the containerized stack with MOCK_LLM=false (Ollama).

Needs: `MOCK_LLM=false docker compose -f deploy/compose.yaml --profile llm up -d --wait` with
AUTH_USERS containing demo/demo-pw. Run: `uv run pytest -m llm -v`.
"""

import json
import subprocess
import uuid

import httpx
import pytest

pytestmark = pytest.mark.llm
AUTH = ("demo", "demo-pw")


def _gateway() -> str:
    """First gateway replica's host URL."""
    out = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "deploy/compose.yaml",
            "port",
            "--index",
            "1",
            "gateway",
            "8000",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return f"http://{out}"


async def test_a9_real_llm_round_trip() -> None:
    """A real model answers: tokens then exactly one done, and the reply is not the mock's."""
    gw, thread, mid = _gateway(), f"a9-{uuid.uuid4().hex[:6]}", str(uuid.uuid4())
    body = {"message": "How do I reset my router?", "thread_id": thread, "message_id": mid}
    async with httpx.AsyncClient(timeout=200) as client:
        r = await client.post(f"{gw}/chat", json=body, auth=AUTH)
        assert r.status_code == 202, r.text
        events = []
        async with client.stream("GET", f"{gw}/stream/{thread}/{mid}", auth=AUTH) as s:
            async for line in s.aiter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "done" and kinds.count("done") == 1, kinds[-3:]
    text = " ".join(e["token"] for e in events if e["type"] == "token")
    assert len(text.split()) >= 5 and not text.startswith("[mock]"), text[:200]
