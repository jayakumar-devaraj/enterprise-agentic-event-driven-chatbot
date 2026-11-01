"""Human approval gate for the refund action (SPEC.md §10, ADR 0006/0007).

The decision to propose a refund is a deterministic rule on scrubbed text, never model output, so
an injected prompt cannot trigger or approve it. Nothing is applied before the owner approves.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

from chat_common.audit import AUDIT_MAXLEN, AUDIT_STREAM, AuditLog

from worker.lease import FENCE, Lease

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


# Marker, refund request and audit entry in one script. Redis runs it in isolation but does not
# roll back on a runtime error, so every argument and key type is checked before the first write;
# after that the writes cannot fail on input. The marker, written last, holds the refund entry id
# (its receipt). A marker whose receipt is missing is an error, never a success. Markers written
# before receipts existed hold "1". FENCE makes a worker that lost its dedupe lease fail first.
_APPLY = (
    FENCE
    + """
local refund, audit = cjson.decode(ARGV[1]), cjson.decode(ARGV[2])
for i = 2, 3 do
  local t = redis.call('TYPE', KEYS[i])['ok']
  if t ~= 'stream' and t ~= 'none' then
    return redis.error_reply('WRONGTYPE refund script: ' .. KEYS[i] .. ' is a ' .. t)
  end
end
local receipt = redis.call('GET', KEYS[1])
if receipt == '1' then return 0 end
if receipt then
  if #redis.call('XRANGE', KEYS[2], receipt, receipt) == 1 then return 0 end
  return redis.error_reply('refund marker ' .. KEYS[1] .. ' has no receipt ' .. receipt)
end
local id = redis.call('XADD', KEYS[2], '*', unpack(refund))
redis.call('XADD', KEYS[3], 'MAXLEN', '~', ARGV[3], '*', unpack(audit))
redis.call('SET', KEYS[1], id)
return 1
"""
)


# The approval window marker and its audit entry in one fenced script, so a worker that lost its
# dedupe lease changes neither and a crash cannot leave one without the other. ARGV[1] is 'set',
# 'del' or 'none' (audit only); an empty audit array writes no entry.
_MARK = (
    FENCE
    + """
if ARGV[1] == 'set' then
  redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
elseif ARGV[1] == 'del' then
  redis.call('DEL', KEYS[1])
end
local audit = cjson.decode(ARGV[4])
if #audit > 0 then
  redis.call('XADD', KEYS[2], 'MAXLEN', '~', ARGV[5], '*', unpack(audit))
end
return 1
"""
)


async def mark(
    redis: Any,
    lease: Lease,
    *,
    pending: str | None = None,
    owner: str | None = None,
    ttl_s: int = 0,
    audit: dict[str, str] | None = None,
) -> None:
    """Open (`owner` given) or close the approval window `pending` and append `audit`, atomically
    and only while `lease` is held (LeaseLost otherwise)."""
    op = "none" if pending is None else ("set" if owner else "del")
    keys = [pending or AUDIT_STREAM, AUDIT_STREAM]
    args = [op, owner or "", ttl_s, _flat(audit or {}), AUDIT_MAXLEN]
    await lease.run(redis, _MARK, keys, args)


def _flat(fields: dict[str, str]) -> str:
    """Stream fields as the JSON array [k1, v1, k2, v2, ...] the script unpacks."""
    return json.dumps([item for pair in fields.items() for item in pair])


async def apply_refund(
    redis: Any, *, thread_id: str, ref_message_id: str, user_id: str, lease: Lease
) -> bool:
    """Record the refund request at most once per originating message; True if recorded now.

    The marker never expires, so the at-most-once guarantee has no retention window. False means
    an earlier call already recorded all three entries. LeaseLost if `lease` is no longer held.
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
    keys = [f"refund_applied:{ref_message_id}", REFUNDS_STREAM, AUDIT_STREAM]
    args = [_flat(refund), _flat(audit), AUDIT_MAXLEN]
    return bool(await lease.run(redis, _APPLY, keys, args))
