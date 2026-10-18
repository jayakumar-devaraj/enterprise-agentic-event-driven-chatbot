"""A2: eval samples route to their labels and in-scope ones are never refused (real Qdrant).

Also pins the injection detector's held-out verdicts (ADR 0004 addendum 2026-10-18) so a change to
seeds or the margin cannot silently regress them.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from chat_common.settings import Settings
from conftest import QDRANT_GRPC_PORT, QDRANT_URL
from qdrant_client import AsyncQdrantClient
from seed.main import seed
from worker.embedding import get_embedder
from worker.router import Router

pytestmark = pytest.mark.integration
DATA = Path(__file__).resolve().parents[2] / "data"
EVAL = DATA / "intents.eval.jsonl"
HOLDOUT = DATA / "guard.holdout.jsonl"


def _rows(path: Path) -> list[dict]:
    """JSONL rows."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture(scope="module")
async def router() -> AsyncIterator[Router]:
    """Seed via the real seed path, then a gRPC Router with the configured cuts."""
    settings = Settings(qdrant_url=QDRANT_URL)
    await seed(settings)
    qdrant = AsyncQdrantClient(url=QDRANT_URL, grpc_port=QDRANT_GRPC_PORT, prefer_grpc=True)
    yield Router(
        get_embedder(settings.embed_model),
        qdrant,
        intents_collection=settings.qdrant_collection,
        injection_collection=settings.qdrant_injection_collection,
        benign_collection=settings.qdrant_benign_collection,
        route_threshold=settings.route_threshold,
        injection_margin=settings.injection_margin,
    )
    await qdrant.close()


@pytest.mark.asyncio(loop_scope="module")
async def test_eval_samples_route_to_their_labels_and_in_scope_ones_are_not_refused(
    router,
) -> None:
    """Every sample routes to its label; no billing/tech/general support question is refused."""
    rows = _rows(EVAL)
    results = [(row, await router.route(row["text"])) for row in rows]
    misrouted = [
        (row["text"], row["label"], r.intent, round(r.score, 3))
        for row, r in results
        if r.intent != row["label"]
    ]
    refused_in_scope = [row["text"] for row, r in results if r.injection and not row["off_topic"]]
    assert len(rows) >= 17 and sum(r["off_topic"] for r in rows) >= 5
    assert misrouted == []
    assert refused_in_scope == []


@pytest.mark.asyncio(loop_scope="module")
async def test_holdout_verdicts_do_not_regress(router) -> None:
    """Measured 2026-10-18: 20/20 injections detected, 4/20 benign refused (ADR 0004)."""
    results = [(row, await router.route(row["text"])) for row in _rows(HOLDOUT)]
    detected = sum(1 for row, r in results if row["label"] == "injection" and r.injection)
    refused = sum(1 for row, r in results if row["label"] == "benign" and r.injection)
    assert detected >= 20
    assert refused <= 4
