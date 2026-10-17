"""A2: every eval sample routes to its label; off-topic samples route to `general` (real Qdrant)."""

import json
from pathlib import Path

import pytest
from chat_common.settings import Settings
from conftest import QDRANT_GRPC_PORT, QDRANT_URL
from qdrant_client import AsyncQdrantClient
from seed.main import seed
from worker.embedding import get_embedder
from worker.router import Router

pytestmark = pytest.mark.integration
EVAL = Path(__file__).resolve().parents[2] / "data" / "intents.eval.jsonl"


async def test_eval_samples_route_to_their_labels() -> None:
    """Seed via the real seed path, then route each held-out sample over gRPC with ADR 0004 cuts."""
    settings = Settings(qdrant_url=QDRANT_URL)
    await seed(settings)
    qdrant = AsyncQdrantClient(url=QDRANT_URL, grpc_port=QDRANT_GRPC_PORT, prefer_grpc=True)
    router = Router(
        get_embedder(settings.embed_model),
        qdrant,
        intents_collection=settings.qdrant_collection,
        injection_collection=settings.qdrant_injection_collection,
        route_threshold=settings.route_threshold,
        injection_threshold=settings.injection_threshold,
    )
    rows = [json.loads(line) for line in EVAL.read_text(encoding="utf-8").splitlines() if line]
    try:
        results = [(row, await router.route(row["text"])) for row in rows]
    finally:
        await qdrant.close()

    misrouted = [
        (row["text"], row["label"], r.intent, round(r.score, 3))
        for row, r in results
        if r.intent != row["label"]
    ]
    assert len(rows) >= 17 and sum(r["off_topic"] for r in rows) >= 5
    assert misrouted == []
