"""Intent routing with local vector math: embed, Qdrant top-1, thresholds (L4 detection)."""

from dataclasses import dataclass
from typing import Literal

from qdrant_client import AsyncQdrantClient

from worker.embedding import Embedder

Intent = Literal["billing", "tech_support", "general"]
INTENTS: tuple[Intent, ...] = ("billing", "tech_support", "general")


@dataclass(frozen=True)
class Route:
    """Routing decision for one message."""

    intent: Intent
    score: float
    injection_score: float
    injection: bool


class Router:
    """Routes a scrubbed message; any Qdrant failure propagates for the worker's retry policy."""

    def __init__(
        self,
        embedder: Embedder,
        qdrant: AsyncQdrantClient,
        *,
        intents_collection: str,
        injection_collection: str,
        route_threshold: float,
        injection_threshold: float,
    ) -> None:
        """Store collaborators and calibrated thresholds (ADR 0004)."""
        self._embedder = embedder
        self._qdrant = qdrant
        self._intents = intents_collection
        self._injection = injection_collection
        self._route_threshold = route_threshold
        self._injection_threshold = injection_threshold

    async def _top1(self, collection: str, vector: list[float]) -> tuple[str | None, float]:
        """Best match label and cosine score; (None, 0.0) for an empty collection."""
        res = await self._qdrant.query_points(collection, query=vector, limit=1, with_payload=True)
        if not res.points:
            return None, 0.0
        point = res.points[0]
        return (point.payload or {}).get("label"), float(point.score)

    async def route(self, text: str) -> Route:
        """Injection check first, then top-1 intent; low confidence falls back to `general`."""
        vector = await self._embedder.aembed_query(text)
        _, inj_score = await self._top1(self._injection, vector)
        label, score = await self._top1(self._intents, vector)
        intent: Intent = label if label in INTENTS and score >= self._route_threshold else "general"
        return Route(
            intent=intent,
            score=score,
            injection_score=inj_score,
            injection=inj_score >= self._injection_threshold,
        )
