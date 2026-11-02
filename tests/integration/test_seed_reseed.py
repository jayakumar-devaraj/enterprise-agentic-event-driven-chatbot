"""Re-running the seed under live searches (real Qdrant): no search ever finds a missing collection.

The seed used to delete and recreate each collection, so a worker routing at that moment failed with
"Collection doesn't exist" and could dead-letter the message (CI on PR #86).
"""

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest
from chat_common.settings import Settings
from conftest import QDRANT_API_KEY, QDRANT_URL
from qdrant_client import AsyncQdrantClient, models
from seed.main import DATA_DIR, drop_collection, seed

pytestmark = pytest.mark.integration


def _client() -> AsyncQdrantClient:
    return AsyncQdrantClient(
        verify=os.environ["INFRA_CA_FILE"], url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30
    )


async def _target(qdrant: AsyncQdrantClient, alias: str) -> str | None:
    return next(
        (a.collection_name for a in (await qdrant.get_aliases()).aliases if a.alias_name == alias),
        None,
    )


async def test_a_collection_seeded_before_aliases_is_replaced_by_one() -> None:
    """A stack seeded before aliases holds a real collection under the name; it becomes an alias."""
    sfx = uuid.uuid4().hex[:8]
    settings = Settings(
        qdrant_url=QDRANT_URL,
        qdrant_collection=f"it-legacy-intents-{sfx}",
        qdrant_injection_collection=f"it-legacy-injection-{sfx}",
        qdrant_benign_collection=f"it-legacy-benign-{sfx}",
    )
    qdrant = _client()
    try:
        await qdrant.create_collection(
            settings.qdrant_collection,
            vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
        )
        await seed(settings)
        target = await _target(qdrant, settings.qdrant_collection)
        assert target and target.startswith(f"{settings.qdrant_collection}_")
        assert (await qdrant.count(settings.qdrant_collection, exact=True)).count > 0
    finally:
        for name in (
            settings.qdrant_collection,
            settings.qdrant_injection_collection,
            settings.qdrant_benign_collection,
        ):
            await drop_collection(qdrant, name)
        await qdrant.close()


async def test_reseeding_never_leaves_a_search_without_its_collection(tmp_path: Path) -> None:
    """Search continuously while the seed runs again with the same data, then with changed data."""
    sfx = uuid.uuid4().hex[:8]
    settings = Settings(
        qdrant_url=QDRANT_URL,
        qdrant_collection=f"it-reseed-intents-{sfx}",
        qdrant_injection_collection=f"it-reseed-injection-{sfx}",
        qdrant_benign_collection=f"it-reseed-benign-{sfx}",
    )
    names = (
        settings.qdrant_collection,
        settings.qdrant_injection_collection,
        settings.qdrant_benign_collection,
    )
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    qdrant = _client()
    try:
        await seed(settings, data)
        errors: list[str] = []
        searches = 0
        stop = asyncio.Event()

        async def search() -> None:
            nonlocal searches
            while not stop.is_set():
                for name in names:
                    try:
                        await qdrant.query_points(name, query=[0.05] * 384, limit=1)
                        searches += 1
                    except Exception as exc:  # noqa: BLE001 (any failure is the finding)
                        errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:80]}")

        before = await _target(qdrant, settings.qdrant_collection)
        task = asyncio.create_task(search())
        await seed(settings, data)  # same data: nothing to rewrite
        assert await _target(qdrant, settings.qdrant_collection) == before
        intents = data / "intents.seed.jsonl"
        extra = {"text": "Can I get a copy of last year's invoices?", "label": "billing"}
        intents.write_text(intents.read_text(encoding="utf-8") + json.dumps(extra) + "\n", "utf-8")
        await seed(settings, data)  # changed data: a new version replaces the old one
        stop.set()
        await task

        assert errors == []
        assert searches > 0
        rows = len(intents.read_text(encoding="utf-8").splitlines())
        assert (await qdrant.count(settings.qdrant_collection, exact=True)).count == rows
        physical = {c.name for c in (await qdrant.get_collections()).collections}
        assert len([c for c in physical if c.startswith(f"{settings.qdrant_collection}_")]) == 1
    finally:
        for name in names:
            await drop_collection(qdrant, name)
        await qdrant.close()
