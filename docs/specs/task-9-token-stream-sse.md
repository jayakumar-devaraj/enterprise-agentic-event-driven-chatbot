# Task 9 — Token stream and SSE

**Purpose:** worker XADDs `TokenEvent`/`DoneEvent`/`ErrorEvent` to `tokens:{thread_id}:{message_id}`
with MAXLEN + TTL; gateway `GET /stream/{thread_id}/{message_id}` relays SSE with `id:` = entry id,
resumes from `Last-Event-ID` (exclusive), closes after done/error, owner check, quiet on disconnect.
**Verify:** A3 integration: strictly increasing `seq` ending with done; late connection gets all from 0;
resume yields no gaps or duplicates; second turn contains no first-turn tokens.
