"""Contract tests for chat_common.schemas (SPEC.md §1)."""

from uuid import uuid4

import pytest
from chat_common.schemas import (
    ChatRequest,
    DoneEvent,
    Envelope,
    StreamEvent,
    TokenEvent,
    stream_key,
)
from pydantic import TypeAdapter, ValidationError


@pytest.mark.parametrize("thread_id", [str(uuid4()), "abc", "A_b-9", "x" * 64])
def test_valid_thread_ids(thread_id: str) -> None:
    """UUIDs and the restricted slug pattern are accepted."""
    assert ChatRequest(message="hi", thread_id=thread_id, message_id=uuid4()).thread_id == thread_id


@pytest.mark.parametrize("thread_id", ["", "x" * 65, "has space", "semi;colon", "ümlaut", "../etc"])
def test_invalid_thread_ids(thread_id: str) -> None:
    """Anything outside the pattern is a caller error."""
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", thread_id=thread_id, message_id=uuid4())


def test_chat_request_rejects_unknown_fields_and_empty_message() -> None:
    """Extra keys and empty messages are rejected."""
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            {"message": "hi", "thread_id": "t", "message_id": str(uuid4()), "role": "system"}
        )
    with pytest.raises(ValidationError):
        ChatRequest(message="", thread_id="t", message_id=uuid4())


def test_message_id_must_be_uuid() -> None:
    """message_id is a client-generated UUID."""
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"message": "hi", "thread_id": "t", "message_id": "nope"})


def test_envelope_approval_fields_required_together() -> None:
    """kind='approval' needs ref_message_id and approved; kind='chat' must not carry them."""
    base = {"message_id": uuid4(), "thread_id": "t", "user_id": "u", "text": "", "trace_id": "0"}
    Envelope(**base)
    Envelope(**base, kind="approval", ref_message_id=uuid4(), approved=True)
    with pytest.raises(ValidationError):
        Envelope(**base, kind="approval")
    with pytest.raises(ValidationError):
        Envelope(**base, ref_message_id=uuid4(), approved=False)


def test_envelope_round_trips_json() -> None:
    """The Kafka value survives JSON serialisation unchanged."""
    env = Envelope(
        message_id=uuid4(), thread_id="t", user_id="u", text="<EMAIL_ADDRESS>", trace_id="ab"
    )
    assert Envelope.model_validate_json(env.model_dump_json()) == env


def test_stream_event_discriminator_and_seq_bounds() -> None:
    """Events parse by `type`; seq is non-negative."""
    adapter: TypeAdapter[StreamEvent] = TypeAdapter(StreamEvent)
    assert isinstance(adapter.validate_json('{"type":"token","seq":0,"token":"hi"}'), TokenEvent)
    assert isinstance(adapter.validate_json('{"type":"done","seq":3}'), DoneEvent)
    with pytest.raises(ValidationError):
        TokenEvent(seq=-1, token="x")


def test_stream_key_format() -> None:
    """One stream per message (ADR 0005)."""
    mid = uuid4()
    assert stream_key("t1", mid) == f"tokens:t1:{mid}"


def test_status_and_citations_are_stream_events_but_not_terminal() -> None:
    """D6: both parse through the discriminated union; neither ends a stream."""
    from chat_common.schemas import TERMINAL_TYPES, CitationsEvent, StatusEvent

    adapter = TypeAdapter(StreamEvent)
    status = adapter.validate_python({"type": "status", "seq": 0, "detail": "searching"})
    cites = adapter.validate_python(
        {"type": "citations", "seq": 3, "citations": [{"doc_key": "a.txt", "page_key": "page:1"}]}
    )
    assert isinstance(status, StatusEvent) and isinstance(cites, CitationsEvent)
    assert not ({"status", "citations"} & TERMINAL_TYPES)
    with pytest.raises(ValidationError):  # a citation carries a location, never document text
        adapter.validate_python(
            {
                "type": "citations",
                "seq": 3,
                "citations": [{"doc_key": "a", "page_key": "p", "text": "x"}],
            }
        )
