# Audit follow-ups: chatbot repo

Source: independent audit of this repo at `b4814dc` (score 85/100), run on 2026-11-03. Line numbers
below were re-checked against `origin/main` at `ffe8c86` on 2026-11-06. The RAG service has its own
list in its repository (`docs/reports/audit-followups-2026-11-06.md`).

Each item is tagged **observed** (reproduced or executed) or **inferred** (read from code, not run).
An inferred item must be reproduced before it is fixed. If it does not reproduce, close the item and
say so instead of changing code.

## How to use this file

Start a session in this repository and give it this prompt:

> Read `docs/reports/audit-followups-2026-11-06.md` and `CLAUDE.md`. Work the items in section 1 in
> order, then section 2. One branch and one PR per item. For each item: state the check that proves
> it done, make the smallest change, run it, commit, push, open the PR, and wait for CI. Do not
> merge until I say so. Stop and ask on anything marked "needs a decision".

Rules that bind every item (all from `CLAUDE.md`):
- No `rebase`, `commit --amend`, `push --force` or squash. Merge commits only.
- No attribution trailers in commit messages. Author is the repo-local noreply identity.
- Docs change in the same commit as the behavior they describe.
- Rung 2 verification (real Kafka, Redis, Qdrant) is required for consumer, DLQ, lease, stream and
  approval changes: `docker compose -f deploy/compose.yaml up -d --wait`, then
  `uv run pytest -m integration`. The Docker VM is limited to 8 GB and a second full stack has hung
  it before: check what is running and ask before starting a stack.
- Never state a result you did not run. Paste the command and its output line in the PR.

---

## 1. Code and test fixes (these move the score)

### 1.1 A non-transient processor error wedges a partition
- **Status:** the unit-level behavior is observed; the production effect is inferred.
- **Where:** `services/worker/worker/consumer.py`, `_run_with_retries` catches only
  `TransientDependencyError`, `RedisError`, `TimeoutError` and `ConnectionError`.
  `services/worker/worker/llm.py` lines 228-230 re-raise every other exception.
- **Observed repro:** with a processor double that raises `ValueError`, `Worker.handle` raised with
  0 commits, 0 DLQ records and the dedupe lease still `processing:<token>`.
- **Inferred effect:** `Worker.run` propagates the error and the process stops; the record is
  redelivered after the lease expires and fails the same way, so the partition never advances.
- **Fix:** after the transient clause, catch `Exception`; write an `ErrorEvent` with a fixed,
  generic `detail` (the frontend shows `detail` for any code other than `dependency_unavailable`,
  `frontend/src/ChatWindow.tsx` line 59, so it must never carry the exception text), send a DLQ
  record (the envelope is already scrubbed), release the lease, commit. `LeaseLost` must still
  propagate. `ErrorEvent.code` is a free `str` (`packages/common/chat_common/schemas.py` line 91), so
  no schema change is needed.
- **Decide first: reuse `dependency_unavailable` or add a new code.** Two other places key on that
  exact string, and a new code must be handled in both or the fix is wrong:
  - `consumer.py` lines 253-264: a redelivery that finds a terminal event already streamed treats any
    code other than `dependency_unavailable` as success and calls `lease.done`. A crash between the
    new ErrorEvent and the commit would then record the message as done with no DLQ record.
  - `services/worker/worker/dlq.py` lines 86-98 (`replay`): it deletes the old stream only for
    `dependency_unavailable`. A replayed record with a new code would be skipped as already finished.
  Reusing the existing code is the smaller change but mislabels a code defect as a dependency fault;
  a new code needs both places updated, with a test for each.
- **Docs in the same PR:** `SPEC.md` §7 (failure path, DLQ replay paragraph) and the failure-modes
  section of `docs/architecture/HLD.md`.
- **Done when:** a unit test in `tests/unit/test_worker_consumer.py` shows ErrorEvent, 1 DLQ record,
  lease released and 1 commit; a second unit test shows redelivery after a crash between the
  ErrorEvent and the commit still reaches the DLQ; a replay test covers the chosen code; an
  integration test against real Kafka and Redis shows the next record on the same partition is
  processed. Existing tests still pass.
- **Rubric effect:** 2a +1, 3a +1.

### 1.2 `worker.main` has no test
- **Status:** observed. `docs/reports/coverage.txt` shows `services/worker/worker/main.py` at 0%
  (73 of 73 statements missed).
- **Fix:** add a startup test (settings that violate the dedupe-lease formula fail startup; a stop
  signal ends the loop cleanly), or write down why it is exempt in `docs/known_limitations.md`.
- **Done when:** the coverage report is regenerated from a CI run on the fixed commit, its header
  names that run and commit, and `scripts/gen_figures.py` output matches `docs/summary.md`.

### 1.3 PII scrubber misses its own targets
- **Status:** claim taken from the repo's reports (`docs/reports/pii-*.md`); not re-run by the audit.
  Structured identifiers 10/14 = 0.71 against a target of at least 0.90; locations 16/30 = 0.53
  against at least 0.80.
- **Where:** `packages/common/chat_common/scrubber.py` line 10 lists 7 entity types.
- **Fix:** read the report to see which 4 identifier cases fail, add recognizers for them, and
  improve location handling. Keep scrubbing off the event loop (`asyncio.to_thread`, a repo invariant).
- **Rule:** the targets and the labeled fixture are pre-registered. Do not change either to pass.
  Re-run the same eval, record the new numbers in a new dated report, and say what changed.

### 1.4 Audit events reach the database through a copy
- **Status:** inferred from `docs/summary.md`: refund and access-denial events go to the Redis audit
  stream and an exporter copies them to the append-only database. A machine crash can lose up to the
  last second of state (ADR 0008).
- **Needs a decision:** write those events straight to the database, or state the exposure in
  `SPEC.md`. Ask before building.

### 1.5 Targets that still fail (product, from `docs/summary.md`)
Do these only after 1.1 to 1.3. Each needs a pre-registered target, a command and a dated report.
- Off-topic questions wrongly refused, 4 of 10 against at most 1. **Needs a decision:**
  `docs/known_limitations.md` says the rules forbid tuning the injection threshold or seed data to
  pass a case.
- Backlog growth 3.6 to 4.7 times against at most 2 times. The runs were about 14 seconds; run a
  longer open-loop run before changing code.
- Real-model full reply p95 63 s against at most 60 s. The run was confounded; rerun it clean.
- Reply quality 3.4 against at least 3.5, scored by the build agent. Get an independent scorer.

---

## 2. Documentation and hygiene

### 2.1 Stale limitation entries (observed)
`docs/known_limitations.md` lines 34-35 and 55 say the "Qdrant stopped while running" case is not
automated. `tests/durability/test_dependency_outage.py` now covers it. Check every entry in the file
against code and CI, and mark each as open, fixed (with the PR) or superseded.
- **Rubric effect:** 1a +1, 4b +1.

### 2.2 Superseded audit and plan documents
`docs/reports/project-audit-2026-10-17.md` and `docs/reports/full-score-recommendations.md` are
tracked and out of date. Add a short "superseded by the audit of 2026-11-03" line at the top of each.
Do not rewrite them.

### 2.3 Orchestration log
Add a row for each PR merged since the last row (`docs/AI_ORCHESTRATION.md`, run
`python3 scripts/check_docs.py` to see what the checker requires). Fill the session column only
when a creating call can be found; otherwise write `unrecorded`. Never infer one.

### 2.4 Provenance (partly cannot be recovered)
No commit carries an agent marker (`CLAUDE.md` section 5 forbids trailers), and the session IDs in
`docs/AI_ORCHESTRATION.md` cannot be checked outside the author's machine. Options: commit a manifest
of session ID, PR and transcript hash from now on, or accept the cap on the provenance points. Do
not add markers to past commits.

### 2.5 Local clone (for the owner, not a code change)
- Local `main` is behind `origin/main`: pull before starting.
- An uncommitted edit may exist on `docs/prompts/coding_challenge_eval_prompt.md`. That file is now
  `docs/prompts/evaluation-rubric.md`: reapply the edit there or drop it.
- Old worktrees (see `git worktree list`) may be removable. Ask before removing any; use
  `git worktree remove` without `--force`.

---

## 3. Expected effect

Estimate, not a measurement: items 1.1, 1.2 and 2.1 are worth about +3 to +4 rubric points (85 to about 88-89). The remaining
points depend on provenance that cannot be added to past commits. Items 1.3 to 1.5 are product
quality and are worth little or nothing on the rubric.
