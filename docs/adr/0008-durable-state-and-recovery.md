# 0008 · Durable state and recovery objectives

**Status:** Accepted 2026-10-17.

## Context
Redis holds every piece of shared mutable state: token streams, LangGraph checkpoints, dedupe
leases, thread owners, refund markers, the `refunds` stream and the `audit:events` trail. It ran
with `appendonly no` and no volume. Kafka wrote its log to the image default `/tmp/kafka-logs`,
inside the container. A crash could lose recent Redis writes, and replacing either container lost
everything, including refunds, audit history, topics and committed offsets (audit P1, 2026-10-17).
The deployment is a single node: no replicas, no managed backups.

## Decision
- Redis: AOF with `appendfsync everysec` on the named volume `redis-data` (`/data`).
- Kafka: `KAFKA_LOG_DIRS=/var/lib/kafka/data` on the named volume `kafka-data`; producers keep
  `enable.idempotence` (acks=all).
- Objectives for this single-node deployment:
  - **RPO:** Redis ≤ 1 s of acknowledged writes on a host or OS crash, and 0 on a process
    crash or a container restart or replacement. Kafka is 0 on a process crash; on a host crash it
    is bounded by the OS page-cache flush, since replication factor 1 provides no second copy.
  - **RTO:** measured from SIGKILL to healthy on the development machine: Redis 4.3 s with a
    26 MB AOF (200k keys), Kafka 16.5 s. Both figures include healthcheck polling.
- Backup: stop Redis, tar the `redis-data` volume, restore by extracting into a new volume
  (README, Local development). Kafka is not backed up. Its records are requests already
  answered or still pending, and losing the log loses only unconsumed requests.
- Verified by `tests/durability/test_persistence.py` (`-m durability`, its own compose project and
  CI job): SIGKILL then restart, container replacement, and Redis volume backup/restore.

## Consequences
- `docker compose down -v` now deletes data deliberately. Plain `down` and `up` keep it.
- With `everysec`, a host crash can lose up to one second of Redis writes. That includes a
  `delivered:` marker or an audit entry for work Kafka already holds, and redelivery plus dedupe
  cover it. `appendfsync always` would close that window at a large latency cost per write.
- An existing stack must be recreated once to pick up the volumes. Kafka then starts empty, and
  `kafka-init` recreates the topics.
- Growth is unbounded for `thread_owner:*`, `refund_applied:*` and the `refunds` stream, each one
  small entry per thread or refund. Retention for these is future work.

## Addendum 2026-10-19 · Replay horizon
Kafka kept records for the broker default `log.retention.hours=168` (7 days, read from the running
broker), but dedupe `done` markers lived only `DEDUPE_TTL_S=86400` (1 day). After lost or reset
offsets (a Kafka restore, or a manual reset), a record processed more than a day earlier would run
again as a new turn: a duplicate reply and history turn. Refunds were safe (the marker never expires).

Decision: `kafka-init` sets `retention.ms=KAFKA_RETENTION_MS` (7 days) and
`segment.ms=KAFKA_SEGMENT_MS` (1 day) on `chat-requests`. It does this with `kafka-configs --alter`,
so it also covers existing topics. Settings validation requires
`DEDUPE_TTL_S × 1000 > KAFKA_RETENTION_MS + KAFKA_SEGMENT_MS`. Deletion is per closed segment, hence
the extra segment. `DEDUPE_TTL_S` defaults to 9 days. Shortening retention to 1 day was rejected,
because a worker outage longer than that would delete accepted but unconsumed requests.

Consequences: `dedupe:*`, `accepted:*` and `delivered:*` keys live 9 days instead of 1, each one
short string per message. The guarantee depends on the topic config that `kafka-init` applies. A
topic changed by hand outside it is not checked at runtime.

## Addendum 2026-10-23 · Recovering across the Redis and Kafka recovery points
Redis and Kafka are restored independently. Restoring an older Redis backup while the worker group's
committed offsets stay where they were silently lost everything processed since the backup: turns,
streams, refund receipts and thread bindings. Kafka would never redeliver those records. History
could also come back with no owner binding, so the next caller would claim it.

Decision: recovery replays from Kafka. After a restore, the worker group is rewound to the earliest
retained record. Dedupe markers outlive retention (addendum above), so the backup's finished messages
are skipped and later ones are reprocessed once against the restored state. The worker re-binds
ownership from the checkpointed history or the envelope (SPEC §3). Gateways stay stopped until the
replay drains. The runbook is in the README, and the guarantee is in SPEC §12. It is verified by
`tests/durability/test_restore_recovery.py`.

Rejected: making Redis the only source of truth with more frequent backups. That narrows the loss
window but never closes it. Rejected: replaying from the backup's timestamp instead of the earliest
record. A message accepted before the backup but finished after it would be skipped and lost.

Consequences: a backup older than `KAFKA_RETENTION_MS` cannot be fully recovered. Replay time grows
with retained records (mostly `done` skips). Gateway-side records written after the backup are not
rebuilt (SPEC §12).
