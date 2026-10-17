# Task 10 — Human approval gate

**Purpose:** billing node proposes a "refund request" (side-effecting: written to Redis stream
`refunds`); LangGraph `interrupt()` pauses before applying; stream emits `ApprovalRequiredEvent`;
`POST /approve/{thread_id}/{message_id}` (owner only) publishes the decision; worker resumes, applies
or rejects; every proposal/decision/application written to `audit:events`; pending approvals expire
after `APPROVAL_TTL_S`.
**Verify:** tests: no refund record exists before approval; approve → exactly one record; reject → none;
non-owner → 403; audit entries present.
