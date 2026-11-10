"""PII scrubbing with Presidio + spaCy (guardrail L3 at ingress, L5 on model output)."""

import asyncio
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine

ENTITIES: tuple[str, ...] = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "IP_ADDRESS",
    "LOCATION",
)

# Recognizers for what spaCy and Presidio's built-ins miss (docs/reports/pii-broad-2026-10-25.md).
# Every pattern is case-insensitive (Presidio's default flags).
_SUFFIX = (
    r"(?:street|st|avenue|ave|road|rd|lane|ln|drive|dr|court|ct|boulevard|blvd|way|place|pl"
    r"|terrace|ter|parkway|pkwy|circle|cir|highway|hwy|square|sq|crescent|close|gardens|grove"
    r"|gate|mews)"
)
# Street names do not contain these words, so "15 minutes to walk" is not an address.
_NOT_STREET_WORD = r"(?!(?:the|a|an|to|at|of|in|on|for|and|or|from|by|with)\b)"
_STREET = (
    r"\b\d{1,6}[a-z]?\s+(?:(?:north|south|east|west|[nsew]\.?)\s+)?"
    rf"(?:{_NOT_STREET_WORD}[a-z][a-z'.-]*\s+){{1,3}}{_SUFFIX}\b\.?"
    r"(?:\s+(?:north|south|east|west)\b)?"
    r"(?:,?\s+(?:apt|apartment|suite|ste|unit|flat)\.?\s*[a-z0-9-]+)?"
)
_PO_BOX = r"\bp\.?\s?o\.?\s+box\s+\d+\b"
# `at`/`dot` written as words, bracketed or spaced; a plain address is Presidio's own. A dot is
# tight or spaced on both sides, so a sentence end ("at home. Thanks") is not a domain.
_AT = r"(?:\s*[(\[{<]\s*at\s*[)\]}>]\s*|\s+at\s+|\s*@\s*)"
_DOT = r"(?:\s*[(\[{<]\s*dot\s*[)\]}>]\s*|\s+dot\s+|\.|\s+\.\s+)"
_LOCAL_DOT = r"(?:\s*[(\[{<]\s*dot\s*[)\]}>]\s*|\s+dot\s+|\.)"
_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_LOCAL = rf"[a-z0-9_%+-]+(?:{_LOCAL_DOT}[a-z0-9_%+-]+)*"
_EMAIL = rf"\b{_LOCAL}{_AT}{_LABEL}(?:{_DOT}{_LABEL})*{_DOT}[a-z]{{2,24}}\b"
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IP_DOT = (
    r"(?:\s*\[\s*\.\s*\]\s*|\s*\(\s*\.\s*\)\s*|\s*[(\[{]\s*dot\s*[)\]}]\s*|\s+dot\s+|\.|\s+\.\s+)"
)
_IP = rf"\b{_OCTET}(?:{_IP_DOT}{_OCTET}){{3}}\b"
# 13-19 digits split by one space, dash, dot, slash or underscore; Luhn decides.
_CARD = r"(?<![\d_])\d(?:[ ._/-]?\d){12,18}(?![\d_])"


def _luhn(digits: str) -> bool:
    """Luhn checksum of a digit string."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


class _SeparatedCardRecognizer(PatternRecognizer):
    """Card numbers with separators Presidio's own recognizer does not accept."""

    def validate_result(self, pattern_text: str) -> bool:
        """Only a Luhn-valid number counts."""
        return _luhn("".join(ch for ch in pattern_text if ch.isdigit()))


def _extra_recognizers() -> list[PatternRecognizer]:
    """Street addresses, obfuscated emails and IPs, and separated card numbers."""
    return [
        PatternRecognizer(
            "LOCATION",
            name="street_address",
            patterns=[Pattern("street", _STREET, 0.6), Pattern("po_box", _PO_BOX, 0.6)],
        ),
        PatternRecognizer(
            "EMAIL_ADDRESS", name="obfuscated_email", patterns=[Pattern("email", _EMAIL, 0.6)]
        ),
        PatternRecognizer("IP_ADDRESS", name="obfuscated_ip", patterns=[Pattern("ip", _IP, 0.6)]),
        _SeparatedCardRecognizer(
            "CREDIT_CARD", name="separated_card", patterns=[Pattern("card", _CARD, 0.6)]
        ),
    ]


class Scrubber:
    """Replaces detected entities with typed placeholders such as `<EMAIL_ADDRESS>`."""

    def __init__(self, spacy_model: str = "en_core_web_lg") -> None:
        """Load the spaCy model once; fails loudly if the model is not installed."""
        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": spacy_model}],
            }
        )
        self._analyzer = AnalyzerEngine(
            nlp_engine=provider.create_engine(), supported_languages=["en"]
        )
        for recognizer in _extra_recognizers():
            self._analyzer.registry.add_recognizer(recognizer)
        self._anonymizer = AnonymizerEngine()

    def analyze(self, text: str) -> list[RecognizerResult]:
        """Return detected spans for the configured entity types."""
        return self._analyzer.analyze(text=text, entities=list(ENTITIES), language="en")

    def scrub(self, text: str) -> str:
        """Synchronous scrub; CPU-bound, so async callers must use `ascrub`."""
        if not text:
            return text
        results = self.analyze(text)
        # Presidio's default "replace" operator renders each span as `<ENTITY_TYPE>`.
        return self._anonymizer.anonymize(text=text, analyzer_results=results).text

    async def ascrub(self, text: str) -> str:
        """Scrub off the event loop (engineering rule: no CPU-bound work inline)."""
        return await asyncio.to_thread(self.scrub, text)


@lru_cache(maxsize=2)
def get_scrubber(spacy_model: str = "en_core_web_lg") -> Scrubber:
    """Process-wide scrubber; model load takes seconds, so build it once."""
    return Scrubber(spacy_model)
