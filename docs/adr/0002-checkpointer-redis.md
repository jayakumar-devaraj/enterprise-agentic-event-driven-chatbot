# 0002 · LangGraph checkpointer on Redis 8

**Status:** Accepted 2026-10-17 after spike (a) passed.

## Context
The brief puts conversation memory in Redis 8 via `langgraph-checkpoint-redis`, with Postgres
(`langgraph-checkpoint-postgres`) as the pre-declared fallback if the pinned checkpointer could not
write and read a checkpoint on the chosen Redis image.

## Decision
Use `AsyncRedisSaver` from `langgraph-checkpoint-redis` 0.5.2 on `redis:8.10.2` (langgraph 1.2.16,
langgraph-checkpoint 4.3.0, redisvl 0.28.0). Checkpoint TTL from `CHECKPOINT_TTL_MIN` via the saver's
`ttl` argument.

Evidence: `tests/integration/test_spike_checkpointer.py` compiles a two-turn graph with one saver,
then reads the stored state through a fresh saver on the same `thread_id`:
```
tests/integration/test_spike_checkpointer.py::test_checkpoint_persists_across_saver_instances PASSED
```

## Consequences
- One stateful store (Redis) serves checkpoints, streams, dedupe, rate limits and audit.
- Postgres is not needed; the fallback is not applied.
- The library logs a `DeprecationWarning` (`get_async_redis_connection will become a coroutine`);
  harmless on 0.5.2, revisit on upgrade.
