"""Human approval gate for the refund action (SPEC.md §10, ADR 0006/0007).

The decision to propose a refund is a deterministic rule on scrubbed text, never model output, so
an injected prompt cannot trigger or approve it. Nothing is applied before the owner approves.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

from chat_common.audit import AUDIT_MAXLEN, AUDIT_STREAM, AuditLog

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


# Marker, refund request and audit entry in one script: Redis runs it atomically, so a failure
# before it runs leaves nothing and a lost reply leaves all three.
_APPLY = """
if not redis.call('SET', KEYS[1], '1', 'NX') then return 0 end
redis.call('XADD', KEYS[2], '*', unpack(cjson.decode(ARGV[1])))
redis.call('XADD', KEYS[3], 'MAXLEN', '~', ARGV[3], '*', unpack(cjson.decode(ARGV[2])))
return 1
"""


def _flat(fields: dict[str, str]) -> str:
    """Stream fields as the JSON array [k1, v1, k2, v2, ...] the script unpacks."""
    return json.dumps([item for pair in fields.items() for item in pair])


async def apply_refund(redis: Any, *, thread_id: str, ref_message_id: str, user_id: str) -> bool:
    """Record the refund request at most once per originating message; True if recorded now.

    The marker never expires, so the at-most-once guarantee has no retention window. False means
    an earlier call already recorded all three entries.
    """
    refund = {
        "thread_id": thread_id,
        "ref_message_id": ref_message_id,
        "user_id": user_id,
        "ts": datetime.now(UTC).isoformat(),
    }
    audit = AuditLog.entry(
        "refund_applied", actor=user_id, thread_id=thread_id, ref_message_id=ref_message_id
    )
    keys = (f"refund_applied:{ref_message_id}", REFUNDS_STREAM, AUDIT_STREAM)
    args = (_flat(refund), _flat(audit), AUDIT_MAXLEN)
    return bool(await redis.eval(_APPLY, len(keys), *keys, *args))
