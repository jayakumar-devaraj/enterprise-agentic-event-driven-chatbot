"""Wire contracts (SPEC.md §1). Every service imports these; none redefines them."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ThreadId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
"""UUID or `[A-Za-z0-9_-]{1,64}`; a canonical UUID already satisfies the pattern."""

TenantId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
"""Document-store tenant a user belongs to (SPEC §3); set by the gateway, never by a request."""


class _Strict(BaseModel):
    """Base for wire models: unknown fields are rejected, instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatRequest(_Strict):
    """Body of `POST /chat`."""

    message: str = Field(min_length=1)
    thread_id: ThreadId
    message_id: UUID


class ChatAccepted(_Strict):
    """202 body of `POST /chat` and `POST /approve`."""

    message_id: UUID
    thread_id: ThreadId
    stream_url: str


class ApprovalRequest(_Strict):
    """Body of `POST /approve/{thread}/{message}`; `message_id` names the follow-up message."""

    message_id: UUID
    approved: bool


class Envelope(_Strict):
    """Kafka value on `chat-requests`. `text` is always scrubbed before it is built."""

    message_id: UUID
    thread_id: ThreadId
    user_id: str = Field(min_length=1)
    text: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trace_id: str
    kind: Literal["chat", "approval"] = "chat"
    ref_message_id: UUID | None = None
    approved: bool | None = None
    tenant_id: TenantId | None = None

    @model_validator(mode="after")
    def _approval_fields(self) -> "Envelope":
        """Approval envelopes must reference the paused message and carry the decision."""
        is_approval = self.kind == "approval"
        has_fields = self.ref_message_id is not None and self.approved is not None
        if is_approval != has_fields:
            raise ValueError(
                "ref_message_id and approved are required exactly when kind='approval'"
            )
        return self


class TokenEvent(_Strict):
    """One streamed token."""

    type: Literal["token"] = "token"
    seq: int = Field(ge=0)
    token: str


class DoneEvent(_Strict):
    """Terminal event: generation finished."""

    type: Literal["done"] = "done"
    seq: int = Field(ge=0)


class ErrorEvent(_Strict):
    """Terminal event: the message could not be processed."""

    type: Literal["error"] = "error"
    seq: int = Field(ge=0)
    code: str
    detail: str


class ApprovalRequiredEvent(_Strict):
    """Terminal event for this message: a side-effecting action awaits the owner's decision."""

    type: Literal["approval_required"] = "approval_required"
    seq: int = Field(ge=0)
    action: Literal["refund_request"] = "refund_request"
    detail: str


class StatusEvent(_Strict):
    """Non-terminal progress note, sent before a slow step (SPEC §6). Carries no reply text."""

    type: Literal["status"] = "status"
    seq: int = Field(ge=0)
    detail: str


class Citation(_Strict):
    """Where an answer came from: a document and its page or tab. Never any document text."""

    doc_key: str
    page_key: str


class CitationsEvent(_Strict):
    """Non-terminal: the sources of a document answer, sent after its last token, before `done`."""

    type: Literal["citations"] = "citations"
    seq: int = Field(ge=0)
    citations: list[Citation]


StreamEvent = Annotated[
    TokenEvent | DoneEvent | ErrorEvent | ApprovalRequiredEvent | StatusEvent | CitationsEvent,
    Field(discriminator="type"),
]
TERMINAL_TYPES = frozenset({"done", "error", "approval_required"})


def stream_key(thread_id: str, message_id: UUID | str) -> str:
    """Redis Stream key for one message's events (ADR 0005)."""
    return f"tokens:{thread_id}:{message_id}"


def owner_key(thread_id: str) -> str:
    """Redis key binding a thread to the user who opened it (SPEC §3)."""
    return f"thread_owner:{thread_id}"
