# Durability evidence at `801138e`

Source: `main` at `801138e` (Merge PR #63). Run: GitHub Actions run
[40105751654](https://github.com/jayakumar-devaraj/enterprise-agentic-event-driven-chatbot/actions/runs/40105751654),
job `durability`
([log](https://github.com/jayakumar-devaraj/enterprise-agentic-event-driven-chatbot/actions/runs/40105751654/job/120906972345)),
conclusion `success`. The same run's `lint-and-unit`, `integration` and `frontend` jobs also
succeeded.

**Command:** `uv run pytest -m durability -v` on `ubuntu-latest`. Each module starts its own compose
project (`chatbot-dur`, separate host ports) from `deploy/compose.yaml`. The restore and
worker-kill modules run the worker container, built from this commit with `--build`. The
persistence module runs only Redis and Kafka. The A4 module runs the production `Worker`, graph
and Router in the test process against the project's Kafka, Redis and Qdrant. The engine is the
mock (`MOCK_LLM=true`). Disruptive steps touch only that project.

| Test | Failure injected | Recovery checked | Result |
|---|---|---|---|
| `test_dependency_outage.py::test_a4_qdrant_stopped_then_restarted` | Qdrant stopped while the worker runs | One `ErrorEvent{dependency_unavailable}`, one DLQ record with the envelope, the lease released and the offset committed; after a Qdrant restart the next message ends in `done` | PASSED |
| `test_persistence.py::test_sigkill_then_restart` | SIGKILL of Redis and Kafka | Redis key, stream entry, Kafka record and committed offset all readable after restart | PASSED |
| `test_persistence.py::test_container_replacement` | `--force-recreate` of Redis and Kafka on the same volumes | Same state readable | PASSED |
| `test_persistence.py::test_redis_volume_backup_and_restore` | Redis volume deleted, restored from the README tar backup | Key readable from the new volume | PASSED |
| `test_restore_recovery.py::test_restore_then_rewind_recovers_each_outcome_once` | Redis restored to a backup taken mid-run (loses batch B, a refund and its binding), then the worker group rewound | Batch A not reprocessed (streams byte-identical); B, the refund receipt and thread bindings rebuilt exactly once, turns in order (SPEC §12) | PASSED |
| `test_worker_kill.py::test_a5_sigkill_during_processing_loses_and_duplicates_nothing` | 3× SIGKILL of the worker under 300 in-flight messages | One `done` and contiguous `seq` per message; every thread's turns in order, one pair per message | PASSED |
| `test_worker_kill.py::test_rebalance_while_processing_loses_and_duplicates_nothing` | Scale 1 → 3, SIGKILL one replica, scale to 2 and 1, with a fresh batch before each step (600 messages) | As above; at least two steps overlapped unfinished work | PASSED |
| `test_worker_kill.py::test_tracing_backend_down_does_not_affect_processing` | Jaeger absent: every OTLP export fails | Export failure present in the worker log; messages complete once and in order | PASSED |

## Limits
- One CI runner, a single-node deployment, the mock engine. Recovery times are not measured here;
  the RTO figures in ADR 0008 come from the development machine.
- The rebalance and kill steps are timed by the wall clock. They guard against vacuous passes
  (work must be pending), but kill points are not exhaustive. Targeted durability boundaries are
  covered in-process by `tests/integration/test_replay_recovery.py`.
- None of these tests shows that a fenced write was actually rejected during a run (ADR 0009). The
  rejection itself is tested in `test_stale_worker_cannot_write_after_another_took_over`.
- These tests were not run on the development machine for this commit: earlier local runs were
  stopped for low host memory. CI is the record.
