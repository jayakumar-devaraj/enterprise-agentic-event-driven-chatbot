"""Scrubber behaviour on the entity types the brief names (real spaCy model, no doubles)."""

import pytest
from chat_common.scrubber import get_scrubber

RAW = {
    "EMAIL_ADDRESS": "jane.doe@example.com",
    "PHONE_NUMBER": "212-555-0187",
    "CREDIT_CARD": "4111 1111 1111 1111",
    "US_SSN": "536-22-8726",  # Presidio rejects published sample SSNs (078-05-1120)
    "IP_ADDRESS": "192.168.10.24",
}
MESSAGE = (
    f"Email me at {RAW['EMAIL_ADDRESS']} or call {RAW['PHONE_NUMBER']}. "
    f"My card is {RAW['CREDIT_CARD']}, SSN {RAW['US_SSN']}, server IP {RAW['IP_ADDRESS']}."
)


@pytest.fixture(scope="module")
def scrubbed() -> str:
    """Scrub the mixed-PII message once for all assertions."""
    return get_scrubber().scrub(MESSAGE)


@pytest.mark.parametrize("entity", list(RAW))
def test_raw_value_removed_and_placeholder_present(scrubbed: str, entity: str) -> None:
    """Every raw value is gone and its typed placeholder is present."""
    assert RAW[entity] not in scrubbed
    assert f"<{entity}>" in scrubbed


def test_person_and_location_replaced() -> None:
    """Names and places become placeholders."""
    out = get_scrubber().scrub("My name is John Smith and I live in Chicago.")
    assert "John Smith" not in out and "<PERSON>" in out
    assert "Chicago" not in out and "<LOCATION>" in out


def test_text_without_pii_unchanged_and_empty_ok() -> None:
    """Non-PII text passes through; empty text is a no-op."""
    text = "How do I reset my router?"
    assert get_scrubber().scrub(text) == text
    assert get_scrubber().scrub("") == ""


async def test_ascrub_matches_scrub() -> None:
    """The off-loop variant returns the same result."""
    assert await get_scrubber().ascrub(MESSAGE) == get_scrubber().scrub(MESSAGE)
