"""A1b harness shape: it measures, it never asserts a recall floor (the lead sets that)."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "eval" / "pii_recall.py"
_spec = importlib.util.spec_from_file_location("pii_recall", _PATH)
assert _spec and _spec.loader
pii_recall = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pii_recall)


def test_fixture_meets_brief_minimums_and_report_shape() -> None:
    """≥ 30 sentences, PERSON and LOCATION labeled, recall in [0, 1], latency reported."""
    report = pii_recall.measure()
    assert report["sentences"] >= 30
    assert set(report["recall"]) == {"LOCATION", "PERSON"}
    assert all(0.0 <= r <= 1.0 for r in report["recall"].values())
    assert report["scrub_latency_ms"]["p95"] > 0
