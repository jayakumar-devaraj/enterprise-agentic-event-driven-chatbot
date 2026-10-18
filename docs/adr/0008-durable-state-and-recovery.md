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
