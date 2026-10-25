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


def test_hard_fixture_covers_every_scrubbed_entity_type() -> None:
    """The harder set labels all seven types and reports what it missed."""
    report = pii_recall.measure(pii_recall.HARD_FIXTURE)
    assert report["sentences"] >= 30
    assert set(report["recall"]) == {
        "CREDIT_CARD",
        "EMAIL_ADDRESS",
        "IP_ADDRESS",
        "LOCATION",
        "PERSON",
        "PHONE_NUMBER",
        "US_SSN",
    }
    assert isinstance(report["missed"], list)


def test_broad_scoring_counts_false_positives_and_redaction(tmp_path: Path) -> None:
    """A labelled email is recalled at precision 1.0; an unlabelled phone is one false positive."""
    fixture = tmp_path / "tiny.jsonl"
    fixture.write_text(
        '{"category": "pos", "text": "Write to jane.doe@example.com today.",'
        ' "entities": [{"type": "EMAIL_ADDRESS", "value": "jane.doe@example.com"}]}\n'
        '{"category": "neg", "text": "Call 212-555-0147 tomorrow.", "entities": []}\n',
        encoding="utf-8",
    )
    report = pii_recall.measure_broad(fixture)
    email, phone = report["per_type"]["EMAIL_ADDRESS"], report["per_type"]["PHONE_NUMBER"]
    assert (email["typed_recall"], email["redaction_recall"], email["precision"]) == (1.0, 1.0, 1.0)
    assert (phone["labeled"], phone["detected_spans"], phone["precision"]) == (0, 1, 0.0)
    assert report["false_positives"] == ["neg PHONE_NUMBER: 212-555-0147"]
    assert pii_recall.wilson(0, 0) is None and pii_recall.wilson(5, 5)[1] == 1.0
