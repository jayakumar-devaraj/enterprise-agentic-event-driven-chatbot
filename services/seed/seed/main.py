"""One-shot seeding of the `intents` and `injection_patterns` collections; fails loudly."""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import numpy as np
from chat_common.logjson import configure_logging
from chat_common.settings import Settings
from qdrant_client import AsyncQdrantClient, models
from worker.embedding import DIM, Embedder, get_embedder

from seed import calibrate

log = logging.getLogger("seed")
DATA_DIR = Path(os.getenv("SEED_DATA_DIR", Path(__file__).resolve().parents[3] / "data"))


def read_jsonl(path: Path) -> list[dict[str, str]]:
    """Read `{"text", "label"}` rows; a missing or empty file is a hard error."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not rows:
        raise RuntimeError(f"seed file {path} is empty")
    return rows


async def load_collection(
    qdrant: AsyncQdrantClient, embedder: Embedder, name: str, rows: list[dict[str, str]]
) -> np.ndarray:
    """Recreate `name` (384-dim cosine) and upsert one point per row; return the vectors."""
    vectors = await asyncio.to_thread(embedder.embed_passages, [r["text"] for r in rows])
    if await qdrant.collection_exists(name):
        await qdrant.delete_collection(name)
    await qdrant.create_collection(
        name, vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE)
    )
    await qdrant.upsert(
        name,
        points=[
            models.PointStruct(id=i, vector=v, payload={"label": r["label"], "text": r["text"]})
            for i, (r, v) in enumerate(zip(rows, vectors, strict=True))
        ],
        wait=True,
    )
    count = (await qdrant.count(name, exact=True)).count
    if count != len(rows):
        raise RuntimeError(f"collection {name} has {count} points, expected {len(rows)}")
    return np.asarray(vectors)


async def seed(settings: Settings, data_dir: Path = DATA_DIR) -> dict[str, float]:
    """Load both collections and return the leave-one-out calibration report."""
    embedder = get_embedder(settings.embed_model)
    intents = read_jsonl(data_dir / "intents.seed.jsonl")
    injections = read_jsonl(data_dir / "injection.seed.jsonl")
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, timeout=30)
    try:
        intent_vecs = await load_collection(qdrant, embedder, settings.qdrant_collection, intents)
        inj_vecs = await load_collection(
            qdrant, embedder, settings.qdrant_injection_collection, injections
        )
    finally:
        await qdrant.close()
    intent_q = np.asarray(
        await asyncio.to_thread(embedder.embed_queries, [r["text"] for r in intents])
    )
    inj_q = np.asarray(
        await asyncio.to_thread(embedder.embed_queries, [r["text"] for r in injections])
    )
    route_t, route_acc = calibrate.route_threshold(
        intent_q, intent_vecs, [r["label"] for r in intents]
    )
    inj_t, inj_acc = calibrate.injection_threshold(inj_q, inj_vecs, intent_q)
    report = {
        "route_threshold": route_t,
        "route_loo_accuracy": route_acc,
        "injection_threshold": inj_t,
        "injection_loo_accuracy": inj_acc,
        "intent_exemplars": len(intents),
        "injection_exemplars": len(injections),
    }
    log.info("seeded", extra=report)
    return report


def main() -> None:
    """Entry point for the compose `seed` service; non-zero exit on any failure."""
    settings = Settings()
    configure_logging(settings.log_level)
    report = asyncio.run(seed(settings))
    json.dump(report, sys.stdout)


if __name__ == "__main__":
    main()
