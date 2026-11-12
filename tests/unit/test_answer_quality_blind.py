"""Agreement statistic of the blind answer-quality re-score (tests/eval/answer_quality_blind.py)."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "eval" / "answer_quality_blind.py"
_SPEC = importlib.util.spec_from_file_location("answer_quality_blind", _PATH)
assert _SPEC and _SPEC.loader
blind = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(blind)


def test_identical_raters_agree_fully() -> None:
    assert blind.cohen_kappa([1, 0, 1, 1], [1, 0, 1, 1]) == 1.0


def test_agreement_no_better_than_chance_is_zero() -> None:
    assert blind.cohen_kappa([1, 1, 0, 0], [1, 0, 1, 0]) == 0.0


def test_opposite_raters_disagree_fully() -> None:
    assert blind.cohen_kappa([1, 0, 1, 0], [0, 1, 0, 1]) == -1.0


def test_constant_identical_ratings_have_no_kappa() -> None:
    """Both raters always say 1: agreement is total by chance, so kappa is undefined."""
    assert blind.cohen_kappa([1, 1, 1], [1, 1, 1]) is None
