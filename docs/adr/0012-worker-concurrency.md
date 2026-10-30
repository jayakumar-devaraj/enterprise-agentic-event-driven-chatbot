# 0012 · Several records in flight per worker

**Status:** Accepted 2026-10-30.

## Context
A worker handled one Kafka record at a time. A model call is mostly waiting on I/O, so a worker served one
conversation for the whole call: with a model that takes about 1.8 s per reply, two workers answered about one
turn per 0.9 s (`docs/reports/latency-2026-10-30-worker-concurrency.md`). Adding replicas helps only up to
`KAFKA_PARTITIONS` (SPEC §4). Two rules had to survive any change: a thread's messages are answered in order
(the record key is `thread_id`), and an offset is committed only after the record's outcome is durable (SPEC §4,
§5). Committing offset n commits every record of the partition below n.

## Decision
- `Worker.run` keeps up to `WORKER_CONCURRENCY` records in flight (default 4; settings reject values below 1).
- Records with the same key form a lane: each starts only after the previous one of that key finished, so a
  thread's messages still run one at a time in offset order. A record whose predecessor failed is not run; the
  failure stops the process as before, and the other records in flight are cancelled as in a crash.
- Per partition, the commit point is the lowest offset still in flight (or the highest finished offset plus one
  when none is). Commits move it forward only, under one lock.
- On stop the loop stops polling and waits for the records in flight.
- Rebalance needs no callback. A record whose partition moved can still finish on the old owner: its stream and
  checkpoint writes are fenced by the dedupe lease (ADR 0009), and a stale commit is rejected by the
  consumer-group generation or at worst moves the group's offset back, which redelivers records that dedupe
  skips.

## Consequences
- With the model call as I/O, throughput rises with the concurrency (about 4× at 4 in the report above).
- When the host is CPU-bound (mock engine), it only moves waiting from Kafka into the worker and takes CPU from
  co-located gateways; it does not lower first-token or completion latency.
- The model server sees up to `WORKER_CONCURRENCY` × replicas concurrent calls and must be sized for them.
- A crash or `LeaseLost` now abandons up to `WORKER_CONCURRENCY` records instead of one; all are redelivered.
- One hot thread still runs serially: concurrency is across threads, never within one.
