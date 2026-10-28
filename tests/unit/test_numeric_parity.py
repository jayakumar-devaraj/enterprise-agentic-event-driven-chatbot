"""The copied numeric check gives the original's answers (golden cases from the service repo)."""

import json
from pathlib import Path

import pytest
from worker.numeric import numeric_groundedness

GOLDEN = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "numeric_parity.json").read_text(encoding="utf-8")
)


def test_golden_file_names_its_source() -> None:
    assert "multi-tenant-agentic-rag" in GOLDEN["source"] and "d2aa34a" in GOLDEN["source"]
    assert len(GOLDEN["cases"]) >= 16


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["answer"][:40] or "<empty>")
def test_matches_the_original(case: dict) -> None:
    ratio, unsupported = numeric_groundedness(case["answer"], case["contexts"])
    assert (ratio, unsupported) == (case["ratio"], case["unsupported"])
