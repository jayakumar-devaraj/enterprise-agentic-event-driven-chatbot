"""A1b: per-entity PII recall and p95 scrub latency over tests/fixtures/pii_labeled.jsonl.

Reports numbers only; the recall floor is set by the lead after the first measurement.
Run: `uv run python tests/eval/pii_recall.py` → prints JSON and writes docs/reports/pii_recall.json.
Harder set (international formats, no separators, lowercase names, obfuscation):
`uv run python tests/eval/pii_recall.py hard` → docs/reports/pii_recall_hard.json.
"""

import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from chat_common.scrubber import get_scrubber

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "pii_labeled.jsonl"
REPORT = ROOT / "docs" / "reports" / "pii_recall.json"
HARD_FIXTURE = ROOT / "tests" / "fixtures" / "pii_hard.jsonl"
HARD_REPORT = ROOT / "docs" / "reports" / "pii_recall_hard.json"


def measure(fixture: Path = FIXTURE) -> dict[str, object]:
    """A labeled value counts as recalled if a detected span of the same type covers it."""
    scrubber = get_scrubber()
    scrubber.scrub("warm-up")  # exclude one-time model initialisation from latency
    rows = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line]
    labeled: Counter[str] = Counter()
    hit: Counter[str] = Counter()
    latencies_ms: list[float] = []
    missed: list[str] = []
    for row in rows:
        text = row["text"]
        spans = scrubber.analyze(text)
        start = time.perf_counter()
        scrubber.scrub(text)
        latencies_ms.append((time.perf_counter() - start) * 1000)
        for ent in row["entities"]:
            labeled[ent["type"]] += 1
            begin = text.index(ent["value"])
            end = begin + len(ent["value"])
            if any(
                s.entity_type == ent["type"] and s.start <= begin and s.end >= end for s in spans
            ):
                hit[ent["type"]] += 1
            else:
                missed.append(f"{ent['type']}: {ent['value']}")
    p95 = statistics.quantiles(latencies_ms, n=20)[18]
    return {
        "sentences": len(rows),
        "spacy_model": "en_core_web_lg",
        "recall": {k: round(hit[k] / labeled[k], 4) for k in sorted(labeled)},
        "counts": {k: {"labeled": labeled[k], "recalled": hit[k]} for k in sorted(labeled)},
        "missed": missed,
        "scrub_latency_ms": {
            "p50": round(statistics.median(latencies_ms), 2),
            "p95": round(p95, 2),
        },
    }


if __name__ == "__main__":
    hard = sys.argv[1:] == ["hard"]
    report = measure(HARD_FIXTURE if hard else FIXTURE)
    out = HARD_REPORT if hard else REPORT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    json.dump(report, sys.stdout, indent=2)
