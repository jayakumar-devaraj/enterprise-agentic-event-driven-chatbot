"""Refusal evaluation (ADR 0004 addendum): current vs margin rule on the held-out guard set.

Scores every sample in data/guard.eval.jsonl and data/intents.eval.jsonl with the production Router
against the seeded Qdrant, then applies both rules to the same scores. Reports numbers only; the
acceptance targets are fixed in ADR 0004.
Run (stack up and seeded): `uv run python tests/eval/guard_eval.py` → prints JSON and writes
docs/reports/guard_eval.json.
"""

import asyncio
import json
import os
from pathlib import Path

from chat_common.settings import Settings
from qdrant_client import AsyncQdrantClient
from worker.embedding import get_embedder
from worker.router import Router

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "data" / "guard.eval.jsonl"
INTENTS = ROOT / "data" / "intents.eval.jsonl"
REPORT = ROOT / "docs" / "reports" / "guard_eval.json"


def _rows(path: Path) -> list[dict]:
    """JSONL rows."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def measure() -> dict[str, object]:
    """Route every sample once; evaluate both rules on the recorded scores."""
    settings = Settings(qdrant_url=os.getenv("QDRANT_URL", "http://127.0.0.1:16333"))
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        grpc_port=int(os.getenv("QDRANT_GRPC_PORT", "16334")),
        prefer_grpc=True,
    )
    router = Router(
        get_embedder(settings.embed_model),
        qdrant,
        intents_collection=settings.qdrant_collection,
        injection_collection=settings.qdrant_injection_collection,
        route_threshold=settings.route_threshold,
        injection_threshold=settings.injection_threshold,
    )
    t = settings.injection_threshold
    rules = {
        "current": lambda inj, top: inj >= t,
        "margin": lambda inj, top: inj >= t and inj > top,
    }
    samples = [(r["text"], r["label"] == "injection") for r in _rows(GUARD)]
    samples += [(r["text"], False) for r in _rows(INTENTS)]
    try:
        scored = []
        for text, is_injection in samples:
            route = await router.route(text)
            scored.append((text, is_injection, route.injection_score, route.score))
    finally:
        await qdrant.close()

    guard_n = len(_rows(GUARD))
    report: dict[str, object] = {"injection_threshold": t, "rules": {}}
    for name, rule in rules.items():
        flagged = [(text, inj) for text, inj, s_inj, s_top in scored if rule(s_inj, s_top)]
        guard = scored[:guard_n]
        report["rules"][name] = {
            "benign_refused": sum(1 for _, inj, si, st in guard if not inj and rule(si, st)),
            "benign_total": sum(1 for _, inj, _, _ in guard if not inj),
            "injections_detected": sum(1 for _, inj, si, st in guard if inj and rule(si, st)),
            "injections_total": sum(1 for _, inj, _, _ in guard if inj),
            "intent_eval_refused": [
                t for t, _ in flagged if t in {s for s, *_ in scored[guard_n:]}
            ],
            "false_refusals": [text for text, inj in flagged if not inj],
            "missed_injections": [text for text, inj, si, st in guard if inj and not rule(si, st)],
        }
    report["scores"] = [
        {
            "text": text,
            "injection": inj,
            "injection_score": round(si, 4),
            "intent_score": round(st, 4),
        }
        for text, inj, si, st in scored
    ]
    return report


if __name__ == "__main__":
    result = asyncio.run(measure())
    REPORT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["rules"], indent=2))
