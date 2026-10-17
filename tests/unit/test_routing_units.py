"""Calibration rule and router decision logic with test doubles (no model, no Qdrant)."""

from dataclasses import dataclass
from typing import Any

import numpy as np
from seed import calibrate
from worker.router import Router


def test_best_threshold_prefers_max_accuracy_then_largest_cut() -> None:
    """Two cuts reach the same accuracy; the larger one wins."""
    items = [(0.9, True, False), (0.8, True, False), (0.5, False, True), (0.4, False, True)]
    t, acc = calibrate._best_threshold(items)
    assert acc == 1.0 and t == 0.8


def test_route_threshold_on_toy_vectors() -> None:
    """Clustered exemplars give perfect LOO accuracy."""
    vecs = np.array([[1, 0], [0.99, 0.14], [0, 1], [0.14, 0.99]], dtype=float)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    labels = ["billing", "billing", "tech_support", "tech_support"]
    t, acc = calibrate.route_threshold(vecs, vecs, labels)
    assert acc == 1.0 and 0 < t <= 1.01


@dataclass
class _Point:
    payload: dict[str, str]
    score: float


@dataclass
class _Res:
    points: list[_Point]


class FakeQdrant:
    """Test double: fixed top-1 per collection."""

    def __init__(self, hits: dict[str, _Point | None]) -> None:
        """Map collection name → best point (or None for empty)."""
        self.hits = hits

    async def query_points(self, collection: str, **_: Any) -> _Res:
        """Return the configured hit."""
        hit = self.hits[collection]
        return _Res([hit] if hit else [])


class FakeEmbedder:
    """Test double: constant vector."""

    async def aembed_query(self, text: str) -> list[float]:
        """Ignore text; routing logic is under test, not embeddings."""
        return [0.0] * 384


def _router(intent_hit: _Point | None, inj_score: float) -> Router:
    """Router over doubles with thresholds 0.6 / 0.7."""
    qdrant = FakeQdrant({"intents": intent_hit, "inj": _Point({"label": "injection"}, inj_score)})
    return Router(
        FakeEmbedder(),  # type: ignore[arg-type]
        qdrant,  # type: ignore[arg-type]
        intents_collection="intents",
        injection_collection="inj",
        route_threshold=0.6,
        injection_threshold=0.7,
    )


async def test_confident_match_routes_to_label() -> None:
    """Score above the cut keeps the matched intent."""
    r = await _router(_Point({"label": "billing"}, 0.8), 0.1).route("x")
    assert (r.intent, r.injection) == ("billing", False)


async def test_low_score_falls_back_to_general() -> None:
    """Below ROUTE_THRESHOLD ⇒ general."""
    r = await _router(_Point({"label": "tech_support"}, 0.59), 0.1).route("x")
    assert r.intent == "general"


async def test_empty_collection_and_injection_flag() -> None:
    """No intent points ⇒ general; injection score at the cut ⇒ flagged."""
    r = await _router(None, 0.7).route("x")
    assert r.intent == "general" and r.injection is True
