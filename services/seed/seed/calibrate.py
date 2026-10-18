"""Leave-one-out threshold calibration over the seed files (ADR 0004).

Rule fixed before any eval data was looked at: pick the threshold with maximum LOO accuracy;
break ties with the largest threshold. For routing that sends uncertain input to the safe default
(`general`); for injection detection it minimises false refusals of legitimate users.
"""

import numpy as np


def _best_threshold(scores_and_correct: list[tuple[float, bool, bool]]) -> tuple[float, float]:
    """Items: (score, correct_if_accepted, correct_if_rejected). Returns (threshold, accuracy)."""
    candidates = sorted({s for s, _, _ in scores_and_correct}) + [1.01]
    best_t, best_acc = 0.0, -1.0
    for t in candidates:
        ok = sum(acc if s >= t else rej for s, acc, rej in scores_and_correct)
        acc = ok / len(scores_and_correct)
        if acc >= best_acc:  # ">=" keeps the largest threshold among ties
            best_t, best_acc = t, acc
    return round(best_t, 4), round(best_acc, 4)


def route_threshold(
    queries: np.ndarray, passages: np.ndarray, labels: list[str]
) -> tuple[float, float]:
    """Hold out each exemplar: score its query-side vector against every other passage vector.

    This is the same quantity the router computes at runtime (query prefix vs stored passages);
    below the threshold the prediction is `general`.
    """
    sims = queries @ passages.T
    np.fill_diagonal(sims, -np.inf)
    items = []
    for i, label in enumerate(labels):
        j = int(np.argmax(sims[i]))
        items.append((float(sims[i, j]), labels[j] == label, label == "general"))
    return _best_threshold(items)


def injection_margin(
    injection_queries: np.ndarray,
    injection_passages: np.ndarray,
    benign_queries: np.ndarray,
    benign_passages: np.ndarray,
) -> tuple[float, float]:
    """Hold out each exemplar and score the router's margin (top-1 injection − top-1 benign).

    Positives are injections (accepted ⇒ correct); negatives are benign exemplars, i.e. the
    intent seeds plus the benign seeds (rejected ⇒ correct). A held-out exemplar never matches
    itself.
    """
    inj_self = injection_queries @ injection_passages.T
    np.fill_diagonal(inj_self, -np.inf)
    ben_self = benign_queries @ benign_passages.T
    np.fill_diagonal(ben_self, -np.inf)
    inj_vs_ben = injection_queries @ benign_passages.T
    ben_vs_inj = benign_queries @ injection_passages.T
    items = [
        (float(inj_self[i].max() - inj_vs_ben[i].max()), True, False)
        for i in range(len(injection_queries))
    ]
    items += [
        (float(ben_vs_inj[i].max() - ben_self[i].max()), False, True)
        for i in range(len(benign_queries))
    ]
    return _best_threshold(items)
