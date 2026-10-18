"""End-to-end demo: ask the chatbot one question and stream the reply.

Run from the project root: `uv run python scripts/end_to_end_demo.py`. Without `--base-url` it finds
the compose gateway itself and starts the stack (`docker compose up -d --wait`) when it is not up.
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "deploy" / "compose.yaml"
DEFAULT_QUESTION = "My Wi-Fi drops every evening at the café. What should I try first?"
TERMINAL = {"done", "error", "approval_required"}
STOP_HINT = f'stop it with: docker compose -f "{COMPOSE_FILE}" stop'


class DemoError(Exception):
    """A failure with a one-line, user-facing explanation."""


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


def run(args: list[str], timeout: float, env: dict[str, str] | None = None) -> str:
    """Run one command (argument list, no shell); return stdout or raise DemoError."""
    try:
        done = subprocess.run(
            args, capture_output=True, encoding="utf-8", timeout=timeout, env=env, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise DemoError(f"`{' '.join(args[:4])} ...` timed out after {timeout:.0f} s") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["no output"]
        raise DemoError(f"`{' '.join(args[:4])} ...` failed: {detail[0]}")
    return done.stdout.strip()


def docker() -> str:
    """Path to a docker CLI whose daemon answers, or DemoError."""
    exe = shutil.which("docker")
    if exe is None:
        raise DemoError("Docker not found on PATH - install Docker Desktop")
    try:
        run([exe, "info"], timeout=30)
    except DemoError as exc:
        raise DemoError("Docker is installed but not running - start Docker Desktop") from exc
    return exe


def gateway_url(exe: str) -> str | None:
    """Host URL of the compose gateway (first replica), or None when it is not published."""
    try:
        hostport = run([exe, "compose", "-f", str(COMPOSE_FILE), "port", "gateway", "8000"], 30)
    except DemoError:
        return None
    return f"http://{hostport}" if hostport else None


def is_our_gateway(base: str) -> bool:
    """True when `base` answers as this repo's gateway (OpenAPI title) and its Redis is up."""
    try:
        with urllib.request.urlopen(f"{base}/openapi.json", timeout=5) as resp:
            if json.load(resp)["info"]["title"] != "chat-gateway":
                return False
        with urllib.request.urlopen(f"{base}/healthz", timeout=5) as resp:
            return json.load(resp).get("status") == "ok"
    except (OSError, ValueError, KeyError):
        return False


def ensure_stack(password: str, user: str) -> str:
    """Find the gateway, starting the compose stack first when needed; return its URL."""
    exe = docker()
    base = gateway_url(exe)
    if base and is_our_gateway(base):
        print(f"Gateway already running at {base}")
        return base
    env = dict(os.environ)
    if not env.get("AUTH_USERS"):
        from gateway.auth import hash_password

        env["AUTH_USERS"] = f"{user}:{hash_password(password)}"
    print("Starting the stack (docker compose up -d --wait; first run builds images) ...")
    run([exe, "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--wait"], timeout=900, env=env)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        base = gateway_url(exe)
        if base and is_our_gateway(base):
            print(f"Gateway ready at {base}; the stack keeps running ({STOP_HINT})")
            return base
        time.sleep(2)
    raise DemoError(f'gateway not ready after 120 s - see `docker compose -f "{COMPOSE_FILE}" ps`')


def ask(default: str) -> str:
    """Prompt for the question on a terminal; otherwise, or on EOF/Ctrl+C, use the default."""
    if not sys.stdin.isatty():
        return default
    try:
        return input(f"Question [{default}]: ").strip() or default
    except (EOFError, KeyboardInterrupt):
        print()
        return default


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", help="the question to ask")
    parser.add_argument("--base-url", help="gateway URL (default: find or start the compose one)")
    parser.add_argument("--user", default="demo")
    parser.add_argument("--password", default="demo-pw")
    parser.add_argument("--debug", action="store_true", help="show tracebacks on failure")
    args = parser.parse_args()
    question = args.question or ask(DEFAULT_QUESTION)
    base = args.base_url or ensure_stack(args.password, args.user)
    auth = "Basic " + base64.b64encode(f"{args.user}:{args.password}".encode()).decode()

    body = {
        "message": question,
        "thread_id": f"demo-{uuid.uuid4().hex[:8]}",
        "message_id": str(uuid.uuid4()),
    }
    show("POST /chat request", body)
    with call("POST /chat", base, "/chat", auth, body) as resp:
        accepted = json.load(resp)
    show("POST /chat", accepted)

    events = []
    with call("GET /stream", base, accepted["stream_url"], auth) as resp:
        for line in resp:
            if line.startswith(b"data:"):
                events.append(json.loads(line[5:]))
                if events[-1]["type"] in TERMINAL:
                    break
    reply = " ".join(e["token"] for e in events if e["type"] == "token")
    last = events[-1] if events else None
    show("GET /stream", {"events": len(events), "reply": reply, "last_event": last})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except KeyboardInterrupt:
        print(f"\nInterrupted. Any compose services already started keep running; {STOP_HINT}")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 — one readable line per failure; traceback via --debug
        if "--debug" in sys.argv:
            traceback.print_exc()
        message = str(exc) if isinstance(exc, DemoError) else f"{type(exc).__name__}: {exc}"
        print(f"Demo failed: {message}")
        sys.exit(1)
