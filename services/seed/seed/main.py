"""One-shot seeding of `intents`, `injection_patterns` and `benign_patterns`; fails loudly."""

import asyncio
import hashlib
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


def _version(model: str, rows: list[dict[str, str]]) -> str:
    """Names the content: the same rows embedded by the same model give the same version."""
    content = json.dumps([model, DIM, rows], sort_keys=True).encode()
    return hashlib.sha256(content).hexdigest()[:12]


async def _alias_target(qdrant: AsyncQdrantClient, name: str) -> str | None:
    """The collection the alias `name` points at, if that alias exists."""
    for alias in (await qdrant.get_aliases()).aliases:
        if alias.alias_name == name:
            return alias.collection_name
    return None


async def load_collection(
    qdrant: AsyncQdrantClient,
    embedder: Embedder,
    name: str,
    rows: list[dict[str, str]],
    model: str,
) -> np.ndarray:
    """Make the alias `name` serve these rows (384-dim cosine), never leaving it unresolvable.

    The points go into `{name}_{version}`, and the alias moves to it in one atomic call, so a search
    by `name` sees the old content or the new, never a missing collection. Unchanged rows rewrite
    nothing. Older versions are deleted only after the switch. A real collection called `name`
    (seeded before aliases) is replaced once, which is the one moment `name` is missing.
    """
    vectors = await asyncio.to_thread(embedder.embed_passages, [r["text"] for r in rows])
    target = f"{name}_{_version(model, rows)}"
    current = await _alias_target(qdrant, name)
    complete = await qdrant.collection_exists(target) and (
        await qdrant.count(target, exact=True)
    ).count == len(rows)
    if current == target and complete:
        log.info("collection unchanged", extra={"collection": name, "version": target})
        return np.asarray(vectors)
    if not complete:
        if await qdrant.collection_exists(target):  # an earlier run died while loading it
            await qdrant.delete_collection(target)
        await qdrant.create_collection(
            target, vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE)
        )
        await qdrant.upsert(
            target,
            points=[
                models.PointStruct(id=i, vector=v, payload={"label": r["label"], "text": r["text"]})
                for i, (r, v) in enumerate(zip(rows, vectors, strict=True))
            ],
            wait=True,
        )
        count = (await qdrant.count(target, exact=True)).count
        if count != len(rows):
            raise RuntimeError(f"collection {target} has {count} points, expected {len(rows)}")
    if current is None and await qdrant.collection_exists(name):
        log.warning("replacing pre-alias collection", extra={"collection": name})
        await qdrant.delete_collection(name)
    switch: list[models.AliasOperations] = [
        models.CreateAliasOperation(
            create_alias=models.CreateAlias(collection_name=target, alias_name=name)
        )
    ]
    if current is not None:
        switch.insert(
            0, models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=name))
        )
    await qdrant.update_collection_aliases(change_aliases_operations=switch)
    for c in (await qdrant.get_collections()).collections:
        if c.name.startswith(f"{name}_") and c.name != target:
            await qdrant.delete_collection(c.name)
    log.info("collection switched", extra={"collection": name, "version": target})
    return np.asarray(vectors)


async def drop_collection(qdrant: AsyncQdrantClient, name: str) -> None:
    """Remove the alias `name` and every version behind it (tests and teardown)."""
    if await _alias_target(qdrant, name) is not None:
        await qdrant.update_collection_aliases(
            change_aliases_operations=[
                models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=name))
            ]
        )
    for c in (await qdrant.get_collections()).collections:
        if c.name == name or c.name.startswith(f"{name}_"):
            await qdrant.delete_collection(c.name)


async def seed(settings: Settings, data_dir: Path = DATA_DIR) -> dict[str, float]:
    """Load the three collections and return the leave-one-out calibration report."""
    embedder = get_embedder(settings.embed_model)
    intents = read_jsonl(data_dir / "intents.seed.jsonl")
    injections = read_jsonl(data_dir / "injection.seed.jsonl")
    benign = read_jsonl(data_dir / "benign.seed.jsonl")
    key = settings.qdrant_api_key
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=key.get_secret_value() if key else None,
        timeout=30,
        verify=settings.infra_ca_file,
    )
    try:
        intent_vecs = await load_collection(
            qdrant, embedder, settings.qdrant_collection, intents, settings.embed_model
        )
        inj_vecs = await load_collection(
            qdrant, embedder, settings.qdrant_injection_collection, injections, settings.embed_model
        )
        benign_vecs = await load_collection(
            qdrant, embedder, settings.qdrant_benign_collection, benign, settings.embed_model
        )
    finally:
        await qdrant.close()

    async def queries(rows: list[dict[str, str]]) -> np.ndarray:
        return np.asarray(
            await asyncio.to_thread(embedder.embed_queries, [r["text"] for r in rows])
        )

    intent_q, inj_q, benign_q = (
        await queries(intents),
        await queries(injections),
        await queries(benign),
    )
    route_t, route_acc = calibrate.route_threshold(
        intent_q, intent_vecs, [r["label"] for r in intents]
    )
    margin, inj_acc = calibrate.injection_margin(
        inj_q, inj_vecs, np.vstack([intent_q, benign_q]), np.vstack([intent_vecs, benign_vecs])
    )
    report = {
        "route_threshold": route_t,
        "route_loo_accuracy": route_acc,
        "injection_margin": margin,
        "injection_loo_accuracy": inj_acc,
        "intent_exemplars": len(intents),
        "injection_exemplars": len(injections),
        "benign_exemplars": len(benign),
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
