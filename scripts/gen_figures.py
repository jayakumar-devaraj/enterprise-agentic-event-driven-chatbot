"""Generate the figures quoted in docs/summary.md from the repository, or check they are current.

    uv run python scripts/gen_figures.py           # rewrite figures.json and the summary block
    uv run python scripts/gen_figures.py --check   # exit 1 if either is out of date (run in CI)

Test counts come from `pytest --collect-only`, the frontend count from the test files, and coverage
from the committed docs/reports/coverage.txt (taken from a CI run; its first line names the commit).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "docs" / "reports" / "figures.json"
SUMMARY = ROOT / "docs" / "summary.md"
COVERAGE = ROOT / "docs" / "reports" / "coverage.txt"
START, END = "<!-- figures:start -->", "<!-- figures:end -->"


def collected(marker: str | None) -> int:
    """Number of tests selected by `-m marker` (the default selection when None)."""
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
    out = subprocess.run(
        cmd + (["-m", marker] if marker else []), cwd=ROOT, capture_output=True, text=True
    ).stdout
    match = re.search(r"(\d+)(?:/\d+)? tests? collected", out)
    if not match:
        raise SystemExit(f"could not read a test count from pytest for marker {marker!r}")
    return int(match.group(1))


def frontend_tests() -> int:
    """Count `it(` / `test(` cases in the frontend test files."""
    pattern = re.compile(r"^\s*(?:it|test)\(", re.MULTILINE)
    return sum(
        len(pattern.findall(p.read_text(encoding="utf-8")))
        for p in (ROOT / "frontend" / "src").glob("*.test.*")
    )


def coverage() -> dict[str, object]:
    """Total coverage percentage and the header line of the committed CI coverage report."""
    lines = COVERAGE.read_text(encoding="utf-8").splitlines()
    total = next(
        (m for line in lines if (m := re.match(r"TOTAL\s+(\d+)\s+(\d+)\s+(\d+)%", line))), None
    )
    if total is None:
        raise SystemExit(f"{COVERAGE.name} has no TOTAL line")
    return {"percent": int(total.group(3)), "statements": int(total.group(1)), "source": lines[0]}


def figures() -> dict[str, object]:
    """All figures quoted in the summary."""
    return {
        "unit_tests": collected(None),
        "integration_tests": collected("integration"),
        "durability_tests": collected("durability"),
        "frontend_tests": frontend_tests(),
        "coverage": coverage(),
    }


def block(f: dict[str, object]) -> str:
    """The summary text generated from `f`."""
    cov = f["coverage"]
    source = str(cov["source"]).rstrip(":")
    return (
        f"{START}\n"
        f"- **Tests collected:** {f['unit_tests']} unit, {f['integration_tests']} integration "
        f"(real Kafka, Redis, Qdrant, Postgres), {f['durability_tests']} durability, "
        f"{f['frontend_tests']} frontend.\n"
        f"- **Coverage:** {cov['percent']} % of {cov['statements']} statements. "
        f'Report header: "{source}".\n'
        f"{END}"
    )


def main() -> int:
    """Write or check the generated files."""
    f = figures()
    text = SUMMARY.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"{SUMMARY.name} has no {START} ... {END} block")
    new_text = re.sub(
        f"{re.escape(START)}.*?{re.escape(END)}", lambda _: block(f), text, flags=re.DOTALL
    )
    new_json = json.dumps(f, indent=2) + "\n"
    if "--check" in sys.argv:
        current = FIGURES.exists() and FIGURES.read_text(encoding="utf-8") == new_json
        stale = [
            n for n, ok in ((FIGURES.name, current), (SUMMARY.name, new_text == text)) if not ok
        ]
        for name in stale:
            print(f"{name} is out of date: run `uv run python scripts/gen_figures.py`")
        return 1 if stale else 0
    FIGURES.write_text(new_json, encoding="utf-8")
    SUMMARY.write_text(new_text, encoding="utf-8")
    print(new_json, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
