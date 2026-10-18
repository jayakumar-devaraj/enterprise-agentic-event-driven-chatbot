"""End-to-end demo: ask the chatbot one question and stream the reply.

Run from the project root with the stack already up: `uv run python scripts/end_to_end_demo.py`.
"""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
import uuid

DEFAULT_QUESTION = "My Wi-Fi drops every evening. What should I try first?"
TERMINAL = {"done", "error", "approval_required"}


def show(step: str, payload: object) -> None:
    """Print one step's name and its response as readable JSON."""
    print(f"== {step}\n{json.dumps(payload, indent=2, ensure_ascii=False)}")


def call(step: str, base: str, path: str, auth: str, body: dict | None = None):
    """Open one request; exit 1 with a readable line when it fails."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, headers={"Authorization": auth})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        return urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as exc:
        print(f"{step} failed: HTTP {exc.code}\n{exc.read().decode('utf-8', 'replace')}")
        sys.exit(1)
    except (urllib.error.URLError, OSError):
        print(f"API not reachable at {base} - is `docker compose up -d` running?")
        sys.exit(1)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", help="the question to ask")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="gateway URL")
    parser.add_argument("--user", default="demo")
    parser.add_argument("--password", default="demo-pw")
    args = parser.parse_args()
    question = args.question or input(f"Question [{DEFAULT_QUESTION}]: ").strip()
    question = question or DEFAULT_QUESTION
    auth = "Basic " + base64.b64encode(f"{args.user}:{args.password}".encode()).decode()

    body = {
        "message": question,
        "thread_id": f"demo-{uuid.uuid4().hex[:8]}",
        "message_id": str(uuid.uuid4()),
    }
    with call("POST /chat", args.base_url, "/chat", auth, body) as resp:
        accepted = json.load(resp)
    show("POST /chat", accepted)

    events = []
    with call("GET /stream", args.base_url, accepted["stream_url"], auth) as resp:
        for line in resp:
            if line.startswith(b"data:"):
                events.append(json.loads(line[5:]))
                if events[-1]["type"] in TERMINAL:
                    break
    reply = " ".join(e["token"] for e in events if e["type"] == "token")
    last = events[-1] if events else None
    show("GET /stream", {"events": len(events), "reply": reply, "last_event": last})


if __name__ == "__main__":
    main()
