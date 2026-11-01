# 0009 · Fenced writes under the dedupe lease

**Status:** Accepted 2026-10-18.

## Context
The dedupe lease (`SET dedupe:{message_id} processing NX EX DEDUPE_LEASE_S`, SPEC §5) keeps a
second worker from starting a message while a first may still finish it. Expiry was the only
safeguard. The settings formula bounds the time spent inside `asyncio.timeout`. It does not bound
work outside that timeout (lease setup, stream reset, DLQ produce), a Redis call with no socket
timeout, or a frozen process. A worker that stalls past its lease can lose its partition to another
worker. The new owner then processes the message again, and the stale worker can still write when it
resumes. It could append tokens to the new owner's finished stream, set or delete the new owner's
lease, or write a checkpoint that replaces a later turn. Kafka already rejects the stale worker's
offset commit, because the consumer-group generation fences it. Redis writes had no equivalent
(R3, `docs/reports/full-score-recommendations.md`).

## Decision
- The lease value is a token unique to each attempt: `processing:<uuid4>`.
- Writes that depend on the lease run as Lua scripts that first compare `dedupe:{message_id}` with
  the token, in the same atomic step. These are stream appends and the stream reset, `done` and
  release of the lease, and the refund apply script. A mismatch fails the script before any write.
- A failed check raises `LeaseLost`. It propagates out of the consumer loop and stops the process
  without committing, the same path as a Redis outage while taking the lease (SPEC §4). The
  restarted worker re-reads from the last committed offset. If the lease had simply expired with no
  new owner, the message is redelivered and processed then.
- Rejected: renewing the lease from a heartbeat. It does not stop a frozen process from writing
  when it resumes, so fencing is still needed. Rejected: WATCH/MULTI transactions. They need a
  retry loop and an extra round trip for every token.

## Consequences
- After a takeover, a stale worker can no longer change the token stream, the lease, or the
  refund records.
- These writes are not fenced atomically: LangGraph checkpoint writes (made by its Redis saver),
  `pending_approval:*` and audit entries written by the graph, and the DLQ produce. The DLQ produce
  is preceded by a lease check. The others follow fenced token writes in the same node, so a stale
  worker reaches them only if it stalls for a full lease between two adjacent awaits. They can then
  duplicate an audit entry or `pending_approval:*`, or replace a checkpoint.
- **Addendum 2026-10-29: checkpoint writes are lease-checked.** The paragraph above was wrong for
  checkpoints. The graph writes one after its input and one after routing, before any token, so a
  worker that loaded the thread and then stalled in routing wrote a checkpoint built on that stale
  history once it resumed. It became the thread's latest and dropped the turns the new owner had
  answered meanwhile (the CI rebalance test on `65894ba` lost one turn; on real Redis,
  `test_stale_worker_cannot_roll_back_history_after_another_took_over` lost four). The worker's saver
  (`worker.checkpoint.FencedRedisSaver`) now checks the lease before every checkpoint and
  pending-writes write and raises `LeaseLost`. The check is not atomic with the saver's write, so a
  stall of a full lease between the two still rolls history back.
- **Addendum 2026-10-30: a fenced head pointer decides the thread's history.** That residual was
  hit: with the seed fix in place, CI on #88 lost turn 29 of a rebalance-test thread. The saver now
  moves `thread_head:{thread_id}` to every checkpoint it writes under a held lease, in one Lua script
  that checks the lease token, and returns the head checkpoint when a thread's current state is read
  (no `checkpoint_id` named). A stale write can still land after its check passes, but it cannot
  move the head (`LeaseLost`), so it stays an orphan checkpoint and the next turn builds on the head
  (`test_stale_checkpoint_that_passed_the_lease_check_does_not_become_history`, real Redis; it fails
  without the head). The head key has the checkpoints' TTL and is refreshed when read. A thread with
  no head (written before this change, or its head expired) falls back to the newest checkpoint.
- **Addendum 2026-10-31: a replayed reply is checkpointed before `done`.** The head pointer did not
  stop the losses: CI on #95 and on `8aa6657` each dropped one turn. Per-thread diagnostics on #96 showed
  the cause. A worker died after the reply node's writes were saved but before the checkpoint that
  applies them. The redelivery read the thread's state, which includes those pending writes, found the
  reply already answered and re-sent it, so the message became `done` without that checkpoint. The next
  turn's run starts from the checkpoint, not the pending writes, and dropped the reply. The replay path
  now applies pending writes (`ainvoke(None)`) before re-sending
  (`test_crash_between_the_reply_writes_and_their_checkpoint`, real Redis; it fails without the fix).
  Several records in flight per worker (ADR 0012) widened the window; it existed before.
- **Addendum 2026-11-01: every step a dead attempt left half-written is finished first.** The same gap
  broke the refund approval: a crash after the resumed approval's writes were saved, before their
  checkpoint, made the redelivery see the thread as no longer paused, so it answered
  `approval_not_pending`, never applied the approved refund, and the next chat lost the thread's history.
  Processing now starts by applying any completed task's pending writes (`ainvoke(None)`) and re-reading
  the state, for every envelope kind; this replaces the replay-only step above
  (`test_refund_crash_at_any_checkpoint_write_leaves_the_clean_outcome` crashes each checkpoint write of
  the refund ask, the approval and the next chat and compares history, refunds, audit and terminal
  events with an uncrashed run; real Redis, it fails without the fix).
- **Addendum 2026-11-01: approval windows and their audit entries are fenced.** Opening, closing and
  expiring `pending_approval:*`, and the `refund_proposed`, `refund_expired`, `refund_abandoned` and
  `refund_rejected` audit entries, now go through one Lua script (`approval.mark`) that checks the lease
  token first, so a stale worker changes neither and a crash cannot leave the marker without its entry.
  Before, a worker whose lease was taken after the graph returned still opened or closed the window
  (`test_stale_worker_cannot_open_close_or_audit_an_approval`, real Redis; it fails without the fence).
  The DLQ produce remains preceded by a non-atomic lease check.
- `LeaseLost` restarts the worker process. This is acceptable because a lease is lost only after a
  stall longer than `DEDUPE_LEASE_S`.
- Every fenced script names the lease key with the keys it writes. A move to Redis Cluster would
  need them in one hash slot, which the refund script already requires.
