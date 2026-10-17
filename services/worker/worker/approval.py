"""Human approval gate for the refund action (SPEC.md §10, ADR 0006/0007).

The decision to propose a refund is a deterministic rule on scrubbed text, never model output, so
an injected prompt cannot trigger or approve it. Nothing is applied before the owner approves.
"""

import re
from datetime import UTC, datetime
from typing import Any

from chat_common.audit import AuditLog

REFUND_RE = re.compile(r"\brefunds?\b", re.IGNORECASE)
REFUNDS_STREAM = "refunds"
APPROVED_TEXT = "Your refund request was approved and has been submitted for processing."
REJECTED_TEXT = "Your refund request was not approved, so no refund was submitted."


def wants_refund(intent: str, text: str) -> bool:
    """A billing message that asks for a refund needs the gate."""
    return intent == "billing" and bool(REFUND_RE.search(text))


def pending_key(thread_id: str, message_id: str) -> str:
    """Marks an approval window opened by `message_id`; expires after APPROVAL_TTL_S."""
    return f"pending_approval:{thread_id}:{message_id}"


async def apply_refund(
    redis: Any, *, thread_id: str, ref_message_id: str, user_id: str, ttl_s: int
) -> bool:
    """Write the refund request at most once per originating message; True if written now."""
    if not await redis.set(f"refund_applied:{ref_message_id}", "1", nx=True, ex=ttl_s):
        return False
    await redis.xadd(
        REFUNDS_STREAM,
        {
            "thread_id": thread_id,
            "ref_message_id": ref_message_id,
            "user_id": user_id,
            "ts": datetime.now(UTC).isoformat(),
        },
    )
    await AuditLog(redis).record(
        "refund_applied", actor=user_id, thread_id=thread_id, ref_message_id=ref_message_id
    )
    return True
