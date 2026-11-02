"""An alert leaves the system: Prometheus fires it, Alertmanager delivers it to the webhook.

Its own compose project (`chatbot-ops-test`) runs Prometheus, Alertmanager and a webhook sink, no
worker at all, so `NoWorkerUp` must fire within its `for: 1m` plus Alertmanager's `group_wait`:
    uv run pytest -m ops
"""

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.ops

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "chatbot-ops-test"
FILES = [
    "-f",
    str(ROOT / "deploy" / "compose.yaml"),
    "-f",
    str(Path(__file__).with_name("sink.override.yaml")),
]
ENV = {
    **os.environ,
    "ALERT_WEBHOOK_URL": "http://webhook-sink:8080/alerts",
    "PROMETHEUS_HOST_PORT": "29090",
    "ALERTMANAGER_HOST_PORT": "29093",
}


def _compose(*args: str) -> None:
    cmd = ["docker", "compose", "-p", PROJECT, *FILES, "--profile", "ops", *args]
    subprocess.run(cmd, env=ENV, check=True, capture_output=True, timeout=600)


@pytest.fixture(scope="module")
def stack() -> Iterator[None]:
    """Prometheus, Alertmanager and the sink; nothing else, so no worker is ever up."""
    _compose("down", "-v")
    _compose("up", "-d", "--wait", "--no-deps", "prometheus", "alertmanager", "webhook-sink")
    yield
    _compose("down", "-v")


def test_no_worker_up_reaches_the_webhook(stack) -> None:
    """The webhook gets a firing NoWorkerUp with its severity and summary."""
    deadline = time.monotonic() + 300
    alerts: list[dict] = []
    while time.monotonic() < deadline:
        payloads = httpx.get("http://127.0.0.1:29094/received", timeout=5).json()
        alerts = [
            a for p in payloads for a in p["alerts"] if a["labels"]["alertname"] == "NoWorkerUp"
        ]
        if alerts:
            break
        time.sleep(5)
    assert alerts, "NoWorkerUp never reached the webhook within 300 s"
    assert alerts[0]["status"] == "firing"
    assert alerts[0]["labels"]["severity"] == "critical"
    assert "No worker" in alerts[0]["annotations"]["summary"]


def test_alertmanager_refuses_to_start_without_a_destination() -> None:
    """Without ALERT_WEBHOOK_URL alerts would go nowhere; the container says so and exits."""
    env = {**ENV, "ALERT_WEBHOOK_URL": ""}
    cmd = ["docker", "compose", "-p", f"{PROJECT}-bare", *FILES, "--profile", "ops",
           "run", "--rm", "--no-deps", "alertmanager"]  # fmt: skip
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    assert result.returncode != 0
    assert "ALERT_WEBHOOK_URL" in result.stderr + result.stdout
