"""PII scrubbing with Presidio + spaCy (guardrail L3 at ingress, L5 on model output)."""

import asyncio
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, RecognizerResult
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
