"""Settings validation, including the dedupe-lease formula (A5 unit part, SPEC.md §5)."""

import pytest
from chat_common.settings import RAG_EXTRA_EVENTS, Settings
from pydantic import ValidationError


def test_defaults_satisfy_lease_formula() -> None:
    """Shipped defaults: 200 > 60*3 + 4*2 = 188."""
    assert Settings().dedupe_lease_s == 200


def test_lease_below_formula_fails_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lease that could expire mid-processing is rejected at construction."""
    monkeypatch.setenv("DEDUPE_LEASE_S", "188")
    with pytest.raises(ValidationError, match="DEDUPE_LEASE_S=188 must exceed"):
        Settings()


def test_lease_formula_tracks_retry_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raising retries without raising the lease fails; raising both passes."""
    monkeypatch.setenv("MAX_RETRIES", "5")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("DEDUPE_LEASE_S", "500")
    assert Settings().max_retries == 5


def test_cors_wildcard_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """No wildcard CORS."""
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, *")
    with pytest.raises(ValueError, match="must not contain"):
        _ = Settings().cors_origin_list


def test_cors_list_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comma-separated origins are trimmed."""
    monkeypatch.setenv("CORS_ORIGINS", " http://a.test ,http://b.test")
    assert Settings().cors_origin_list == ["http://a.test", "http://b.test"]


def test_secrets_not_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Secret values never appear in repr (and so never in logs)."""
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-very-secret")
    assert "sk-very-secret" not in repr(Settings())


def test_stream_maxlen_must_hold_a_whole_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trimmed stream would leave a resuming client with a seq gap; a reply never fills it."""
    monkeypatch.setenv("RAG_MAX_TOKENS", "100")  # keep the plain-reply bound the binding one
    monkeypatch.setenv("STREAM_MAXLEN", "258")
    with pytest.raises(ValidationError, match="STREAM_MAXLEN=258 must exceed"):
        Settings()
    monkeypatch.setenv("STREAM_MAXLEN", "259")
    assert Settings().stream_maxlen == 259


def test_dedupe_ttl_must_outlive_kafka_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kafka can redeliver a record until retention plus one segment; dedupe must remember it."""
    s = Settings()
    assert s.dedupe_ttl_s * 1000 > s.kafka_retention_ms + s.kafka_segment_ms
    monkeypatch.setenv("DEDUPE_TTL_S", "86400")
    with pytest.raises(ValidationError, match="DEDUPE_TTL_S=86400 must exceed"):
        Settings()
    monkeypatch.setenv("KAFKA_RETENTION_MS", "43200000")
    monkeypatch.setenv("KAFKA_SEGMENT_MS", "3600000")
    assert Settings().dedupe_ttl_s == 86400


def test_stream_maxlen_must_also_hold_a_whole_document_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document reply adds a status, the citations and the caveat to its tokens (SPEC §6)."""
    bound = 256 + 2 + RAG_EXTRA_EVENTS
    monkeypatch.setenv("STREAM_MAXLEN", str(bound))
    with pytest.raises(ValidationError, match=f"STREAM_MAXLEN={bound} must exceed"):
        Settings()
    monkeypatch.setenv("STREAM_MAXLEN", str(bound + 1))
    assert Settings().stream_maxlen == bound + 1


def test_rag_deadline_must_leave_room_to_answer_when_rag_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RAG_TIMEOUT_S lives inside one attempt: it must be shorter than INFERENCE_TIMEOUT_S."""
    monkeypatch.setenv("INFERENCE_TIMEOUT_S", "15")
    monkeypatch.setenv("DEDUPE_LEASE_S", "100")
    assert Settings().rag_timeout_s == 20  # RAG off (no tokens): the deadline is never used
    monkeypatch.setenv("RAG_TENANT_TOKENS", "tok:T1")
    with pytest.raises(
        ValidationError, match="RAG_TIMEOUT_S=20 must be below INFERENCE_TIMEOUT_S=15"
    ):
        Settings()
    monkeypatch.setenv("RAG_TIMEOUT_S", "10")
    assert Settings().rag_timeout_s == 10


def test_lease_formula_already_covers_a_rag_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The attempt timeout wraps the whole turn, RAG call included, so adding RAG adds no term."""
    monkeypatch.setenv("RAG_TENANT_TOKENS", "tok:T1")
    s = Settings()
    worst = s.inference_timeout_s * (s.max_retries + 1) + s.retry_backoff_max_s * s.max_retries
    assert s.dedupe_lease_s > worst and s.rag_timeout_s < s.inference_timeout_s
