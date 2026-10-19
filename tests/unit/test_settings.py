"""Settings validation, including the dedupe-lease formula (A5 unit part, SPEC.md §5)."""

import pytest
from chat_common.settings import Settings
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
