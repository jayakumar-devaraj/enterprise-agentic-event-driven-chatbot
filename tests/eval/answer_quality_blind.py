"""Independent re-scoring of the 2026-10-25 answer-quality replies (item 1.5).

`export` writes a sheet for a human scorer. It holds each question, its key points, the reply and
the deterministic checks, in a shuffled order, and leaves out the build agent's scores and reasons.
`score <csv>` reads the filled sheet, applies the pre-registered targets
(`answer-quality-rubric-2026-10-25.md`) and reports agreement with the build agent's scores.
Run: `uv run python tests/eval/answer_quality_blind.py export`
     `uv run python tests/eval/answer_quality_blind.py score <filled.csv>`
"""

import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "docs" / "reports"
REPLIES = REPORTS / "answer_quality_replies.json"
AGENT = REPORTS / "answer_quality_scores.json"
SHEET = REPORTS / "answer-quality-blind-sheet-2026-11-10.csv"
RESULT = REPORTS / "answer_quality_independent.json"
CRITERIA = ("C1", "C2", "C3", "C4", "C5")
SEED = 20261110  # fixed, so the order is reproducible


def export() -> None:
    """Write the blind sheet; C1..C5 and note are left for the scorer."""
    agent = {i["id"]: i["deterministic"] for i in json.loads(AGENT.read_text("utf-8"))["items"]}
    replies = json.loads(REPLIES.read_text("utf-8"))
    random.Random(SEED).shuffle(replies)
    with SHEET.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "question", "key_points", "reply", "checks", *CRITERIA, "note"])
        for r in replies:
            checks = ", ".join(f"{k}={v}" for k, v in agent[r["id"]].items())
            w.writerow([r["id"], r["text"], r["key_points"], r["reply"], checks, *[""] * 6])


def cohen_kappa(a: list[int], b: list[int]) -> float | None:
    """Agreement beyond chance for two binary raters; None when chance agreement is total."""
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    return None if chance == 1 else round((observed - chance) / (1 - chance), 3)


def score(path: Path) -> dict[str, object]:
    """Totals, verdicts against the pre-registered targets, and agreement with the agent."""
    with path.open(encoding="utf-8") as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}
    agent = {i["id"]: i for i in json.loads(AGENT.read_text("utf-8"))["items"]}
    if set(rows) != set(agent):
        raise ValueError("the sheet must score exactly the 20 published replies")
    human = {i: {c: int(rows[i][c]) for c in CRITERIA} for i in sorted(rows)}
    if any(v not in (0, 1) for s in human.values() for v in s.values()):
        raise ValueError("every criterion is scored 0 or 1")
    totals = {c: sum(s[c] for s in human.values()) for c in CRITERIA}
    mean = round(sum(totals.values()) / len(human), 2)
    replies = {r["id"]: r for r in json.loads(REPLIES.read_text("utf-8"))}
    empty = sum(1 for r in replies.values() if not r["reply"].strip())
    return {
        "scorer": "project owner, blind to the build agent's scores",
        "totals": totals,
        "mean_score": mean,
        "verdicts": {
            "C3 met 20/20": totals["C3"] == 20,
            "C4 met >= 18/20": totals["C4"] >= 18,
            "mean >= 3.5": mean >= 3.5,
            "empty replies <= 1": empty <= 1,
        },
        "agreement_with_build_agent": {
            c: {
                "same": sum(human[i][c] == agent[i][c] for i in human),
                "kappa": cohen_kappa([human[i][c] for i in human], [agent[i][c] for i in human]),
            }
            for c in CRITERIA
        },
        "items": {i: {**human[i], "note": rows[i]["note"]} for i in human},
    }


if __name__ == "__main__":
    if sys.argv[1:] == ["export"]:
        export()
        print(f"wrote {SHEET.relative_to(ROOT)}")
    elif len(sys.argv) == 3 and sys.argv[1] == "score":
        result = score(Path(sys.argv[2]))
        RESULT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items() if k != "items"}, indent=2))
    else:
        sys.exit(__doc__)
