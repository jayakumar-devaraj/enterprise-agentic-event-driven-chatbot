# 0005 · One Redis Stream per message

**Status:** Accepted 2026-10-17.

## Context
Tokens must reach the client through any gateway replica, survive a late or dropped connection, and
never mix turns. Options: WebSocket from the worker, Kafka topic per thread, Redis Pub/Sub, one
Redis Stream per thread, one Redis Stream per message.

## Decision
Each message gets `tokens:{thread_id}:{message_id}`. Events carry `seq` from 0; SSE `id:` is the
Redis entry id; readers start at `0` or after `Last-Event-ID`; the stream is capped with
`MAXLEN ~ STREAM_MAXLEN` and expires after `STREAM_TTL_S`; the relay closes after `done`/`error`/
`approval_required`.

## Consequences
- Late joiners and resumes are exact (replay from the log), satisfying A3.
- A second turn can never leak tokens from the first, because it reads a different key.
- Pub/Sub was rejected (no replay); per-thread streams were rejected (need cursor bookkeeping per
  turn); WebSockets were rejected (sticky connections break the stateless gateway).
- Cost: one key per message; bounded by MAXLEN and TTL.
