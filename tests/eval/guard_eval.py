"""Refusal evaluation (ADR 0004 addenda): the production Router's injection verdicts per dataset.

Routes every sample with the production Router against the seeded Qdrant and counts verdicts.
`data/guard.holdout.jsonl` is the primary held-out set; `data/guard.eval.jsonl` is informational
(seen before the seeds were written); `data/intents.eval.jsonl` must have no refusals. Reports
numbers only; the acceptance targets are fixed in ADR 0004.
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
DATASETS = {
    "holdout": ROOT / "data" / "guard.holdout.jsonl",
    "guard_eval_informational": ROOT / "data" / "guard.eval.jsonl",
    "intents_eval": ROOT / "data" / "intents.eval.jsonl",
}
REPORT = ROOT / "docs" / "reports" / "guard_eval.json"


def _rows(path: Path) -> list[dict]:
    """JSONL rows."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def measure() -> dict[str, object]:
    """Route every sample once and summarise refusals per dataset."""
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
        benign_collection=settings.qdrant_benign_collection,
        route_threshold=settings.route_threshold,
        injection_margin=settings.injection_margin,
    )
    report: dict[str, object] = {"injection_margin": settings.injection_margin}
    try:
        for name, path in DATASETS.items():
            scored = []
            for row in _rows(path):
                route = await router.route(row["text"])
                truth = row["label"] == "injection"
                scored.append(
                    (row["text"], truth, route.injection, round(route.injection_score, 4))
                )
            report[name] = {
                "benign_refused": sum(1 for _, t, v, _ in scored if not t and v),
                "benign_total": sum(1 for _, t, _, _ in scored if not t),
                "injections_detected": sum(1 for _, t, v, _ in scored if t and v),
                "injections_total": sum(1 for _, t, _, _ in scored if t),
                "false_refusals": [x for x, t, v, _ in scored if not t and v],
                "missed_injections": [x for x, t, v, _ in scored if t and not v],
                "margins": [{"text": x, "injection": t, "margin": m} for x, t, _, m in scored],
            }
    finally:
        await qdrant.close()
    return report


if __name__ == "__main__":
    result = asyncio.run(measure())
    REPORT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    summary = {
        k: {f: v[f] for f in v if f != "margins"} for k, v in result.items() if isinstance(v, dict)
    }
    print(json.dumps(summary, indent=2))
