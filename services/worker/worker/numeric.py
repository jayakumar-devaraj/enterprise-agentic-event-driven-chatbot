"""Numeric-groundedness check for document answers (SPEC.md §13).

COPIED, not imported: from the RAG service repo (jayakumar-devaraj/multi-tenant-agentic-rag),
`src/eval/triad.py`, `_NUMBER`, `_numbers` and `numeric_groundedness`, at commit d2aa34a. It is the
only code taken from that repo; the worker depends on the service's image, never its source.
`tests/unit/test_numeric_parity.py` holds it to the original's outputs. Change both together.
"""

import re

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def numeric_groundedness(answer: str, contexts: list[str]) -> tuple[float, list[str]]:
    """Fraction of numbers in the answer found in the context, plus the unsupported ones."""
    claimed = _numbers(answer)
    if not claimed:
        return 1.0, []
    available = set().union(*(_numbers(c) for c in contexts)) if contexts else set()
    unsupported = sorted(claimed - available)
    return 1 - len(unsupported) / len(claimed), unsupported
