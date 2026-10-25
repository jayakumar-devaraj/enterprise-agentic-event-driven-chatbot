"""A1b: per-entity PII recall and p95 scrub latency over tests/fixtures/pii_labeled.jsonl.

Reports numbers only; the recall floor is set by the lead after the first measurement.
Run: `uv run python tests/eval/pii_recall.py` → prints JSON and writes docs/reports/pii_recall.json.
Harder set (international formats, no separators, lowercase names, obfuscation):
`uv run python tests/eval/pii_recall.py hard` → docs/reports/pii_recall_hard.json.
Broad set with negatives, precision and intervals, targets in pii-targets-2026-10-25.md:
`uv run python tests/eval/pii_recall.py broad` → docs/reports/pii_broad.json.
"""

import json
import math
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
BROAD_FIXTURE = ROOT / "tests" / "fixtures" / "pii_broad.jsonl"
BROAD_REPORT = ROOT / "docs" / "reports" / "pii_broad.json"


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


def wilson(hits: int, n: int) -> list[float] | None:
    """95% Wilson score interval for hits/n; None when n is 0."""
    if n == 0:
        return None
    z, p = 1.96, hits / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)]


def measure_broad(fixture: Path = BROAD_FIXTURE) -> dict[str, object]:
    """Per type: typed recall, redaction recall (covered by a span of any type) and precision.

    A detected span is a true positive if it overlaps a labelled entity of its type, otherwise a
    false positive; every PII value in a sentence is labelled, and negatives have none.
    """
    scrubber = get_scrubber()
    rows = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line]
    labeled, typed, redacted, tp, fp = (Counter() for _ in range(5))
    by_category: dict[str, Counter[str]] = {}
    misses: list[str] = []
    false_positives: list[str] = []
    for row in rows:
        text, spans = row["text"], scrubber.analyze(row["text"])
        cat = by_category.setdefault(row["category"], Counter())
        cat["sentences"] += 1
        located = []
        for ent in row["entities"]:
            begin = text.index(ent["value"])
            end, kind = begin + len(ent["value"]), ent["type"]
            located.append((kind, begin, end))
            labeled[kind] += 1
            cat["labeled"] += 1
            covering = [s for s in spans if s.start <= begin and s.end >= end]
            typed[kind] += any(s.entity_type == kind for s in covering)
            redacted[kind] += bool(covering)
            cat["redacted"] += bool(covering)
            if not covering:
                misses.append(f"{row['category']} {kind}: {ent['value']}")
        for s in spans:
            if any(k == s.entity_type and s.start < e and s.end > b for k, b, e in located):
                tp[s.entity_type] += 1
            else:
                fp[s.entity_type] += 1
                cat["false_positives"] += 1
                false_positives.append(
                    f"{row['category']} {s.entity_type}: {text[s.start : s.end]}"
                )
    types = sorted(set(labeled) | set(fp))
    return {
        "sentences": len(rows),
        "spacy_model": "en_core_web_lg",
        "per_type": {
            k: {
                "labeled": labeled[k],
                "typed_recall": round(typed[k] / labeled[k], 4) if labeled[k] else None,
                "redaction_recall": round(redacted[k] / labeled[k], 4) if labeled[k] else None,
                "redaction_recall_ci95": wilson(redacted[k], labeled[k]),
                "detected_spans": tp[k] + fp[k],
                "precision": round(tp[k] / (tp[k] + fp[k]), 4) if tp[k] + fp[k] else None,
                "precision_ci95": wilson(tp[k], tp[k] + fp[k]),
            }
            for k in types
        },
        "by_category": {k: dict(v) for k, v in sorted(by_category.items())},
        "misses": misses,
        "false_positives": false_positives,
    }


if __name__ == "__main__":
    if sys.argv[1:] == ["broad"]:
        report, out = measure_broad(), BROAD_REPORT
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
        sys.exit(0)
    hard = sys.argv[1:] == ["hard"]
    report = measure(HARD_FIXTURE if hard else FIXTURE)
    out = HARD_REPORT if hard else REPORT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    json.dump(report, sys.stdout, indent=2)
