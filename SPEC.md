# SPEC — contracts

Normative data contract. Anything not written here is not guaranteed. Source: the brief's
"Contracts" section (`docs/prompts/problem-statement.md`) plus the deviations approved in
`docs/adr/0006-approved-deviations.md`. Rationale lives in `docs/adr/`; design in `docs/architecture/`.

## 1 · Schemas (Pydantic v2, defined once in `packages/common`)

| Model | Fields |
|---|---|
| `ChatRequest` | `message: str`, `thread_id: str`, `message_id: UUID` |
| `ChatAccepted` | `message_id`, `thread_id`, `stream_url` |
| `Envelope` (Kafka value) | `message_id`, `thread_id`, `user_id`, `text` (scrubbed), `ts`, `trace_id`, `kind: "chat" \| "approval"`, `ref_message_id?`, `approved?` |
| `TokenEvent` | `seq`, `token` |
| `DoneEvent` | `seq` |
| `ErrorEvent` | `seq`, `code`, `detail` |
| `ApprovalRequiredEvent` | `seq`, `action: "refund_request"`, `detail` |
| `ApprovalRequest` (body of `/approve`) | `message_id: UUID` (new), `approved: bool` |

`thread_id`: UUID or `[A-Za-z0-9_-]{1,64}`. `message_id`: client-generated UUID.

## 2 · HTTP API (gateway :8000, HTTP Basic on every endpoint except `/healthz` and `/readyz`)

| Endpoint | Success | Errors |
|---|---|---|
| `POST /chat` | 202 `ChatAccepted`, only after the broker acknowledged the record; a repeated `message_id` with the same request returns the same 202 body, and one whose earlier attempt was never acknowledged (or not audited) is produced again; reuse for a different request is rejected for `DEDUPE_TTL_S` after acceptance | 401 credentials · 403 thread owned by another user · 413 message over `MAX_MESSAGE_CHARS` · 422 invalid body, `thread_id`, or `message_id` reused for a different request (other thread, text or decision) · 429 per-thread rate limit · 503 Kafka or Redis unavailable |
| `GET /stream/{thread_id}/{message_id}` | 200 `text/event-stream` | 401 · 403 · 404 no stream and the message was never accepted · 422 · 503 |
| `POST /approve/{thread_id}/{message_id}` | 202 `ChatAccepted` for the new message; a repeat of an already-delivered decision returns the same 202 even after the approval was consumed | 401 · 403 · 404 no pending approval (and no delivered decision under this `message_id`) · 422 · 503 |
| `GET /healthz` | 200 while the process serves requests (liveness; no dependency checks) | — |
| `GET /readyz` | 200 when Redis answers and the Kafka request topic is reachable (readiness; the image healthcheck) | 503 dependency down |

4xx = caller fault, 5xx = infrastructure fault, at every boundary. CORS allowlist from
`CORS_ORIGINS`, no wildcard.

## 3 · Access control

The first authenticated `POST /chat` on a `thread_id` binds it to that user
(`thread_owner:{thread_id}`); every later chat, stream or approve call on that thread by another
user returns 403. The binding never expires, so it outlives every piece of thread data: its
checkpoints, token streams, pending approval and refund records. No deletion path exists. A
`thread_id` stays bound to its first user and is never reusable by anyone else, and bindings grow by
one short key per thread (ADR 0008). Deleting a thread would have to remove its data before its
binding. The worker does not rely on the gateway alone for refund decisions (§10).

## 4 · Kafka

Topics `chat-requests` (partitions = `KAFKA_PARTITIONS`, key = `thread_id`) and
`chat-requests.dlq`. Per-thread order comes from the key; worker replicas ≤ `KAFKA_PARTITIONS`.
Changing the partition count remaps keys and is an ADR decision, never a runtime tweak. Offsets are
committed manually and only after the outcome is durable (stream event written, or DLQ written).

## 5 · Worker idempotency

`SET dedupe:{message_id} processing:<token> NX EX DEDUPE_LEASE_S`, with a token unique to each
attempt. On success set `done` with `DEDUPE_TTL_S`. On terminal failure delete the key. `done` ⇒ skip
and commit; `processing:*` ⇒ do not commit; the lease expires and the message is redelivered.
Settings validation fails startup unless
`DEDUPE_LEASE_S > INFERENCE_TIMEOUT_S × (MAX_RETRIES + 1) + RETRY_BACKOFF_MAX_S × MAX_RETRIES`.

**Replay horizon.** Kafka keeps `chat-requests` records for `KAFKA_RETENTION_MS` (set on the topic by
`kafka-init`, with `segment.ms = KAFKA_SEGMENT_MS`), so after lost or reset offsets it can redeliver
a record for at most their sum. Settings validation fails startup unless
`DEDUPE_TTL_S × 1000 > KAFKA_RETENTION_MS + KAFKA_SEGMENT_MS` (defaults: 9 days > 7 + 1 days), so any
record Kafka can still redeliver finds its `done` marker and is skipped, also after later turns on
the thread. If a `done` marker itself is lost (Redis loses up to 1 s of writes on a host crash, ADR
0008), only a redelivery of the thread's latest answered message is recognised (from the checkpoint).
An older one would run again as a new turn.

Writes under the lease are fenced (ADR 0009). Token-stream appends and resets, `done`, release and
the refund apply each check the token in the same Lua script, and fail before writing unless the
lease still holds it. The DLQ produce is preceded by a lease check. A failed check stops the worker
process without committing, so a worker that stalled past its lease cannot change the stream,
lease or refund records of the attempt that took over. Checkpoint, `pending_approval:*` and audit
writes are not fenced atomically (ADR 0009, Consequences).

## 6 · Token stream

One Redis Stream per message, `tokens:{thread_id}:{message_id}`; `seq` starts at 0; `MAXLEN` and TTL
from config. SSE `id:` is the Redis entry id; the payload carries `seq`. Read from `0`, or from
`Last-Event-ID` (exclusive) when sent. Close after `done`, `error` or `approval_required`. A client
disconnect stops the relay without error logs.

A reply never exceeds `LLM_MAX_TOKENS` tokens, one redaction token and a terminal event, and settings
validation fails startup unless `STREAM_MAXLEN > LLM_MAX_TOKENS + 2`, so a stream is never trimmed and
a resume never meets a gap. A stream that no longer exists, for a message that finished (dedupe
`done`), expired after `STREAM_TTL_S`: the response is a single terminal
`ErrorEvent{seq: 0, code: "stream_expired"}` with no `id:`, sent at once whether or not
`Last-Event-ID` is given. A missing stream for a message that is still queued or running is waited
for, as before.

Redelivery after a worker crash advances the conversation by one turn at most. A stream that already
ends in a terminal event is kept unchanged and only the outcome is recorded. A reply already
checkpointed is re-sent from the checkpoint, not regenerated. A partial attempt (tokens, no
terminal event) is replaced: the new attempt starts again at `seq` 0, so a client that sees `seq` 0
after later events discards what it has shown for that message.

## 7 · Failure path

Infra fault (Qdrant / LLM / Redis) → bounded retries with backoff → `ErrorEvent{code:
"dependency_unavailable"}` to the stream, record to the DLQ, then commit. Malformed envelope → DLQ
immediately, no retry.

## 8 · Routing and seeding

Qdrant collection `intents` (384-dim, cosine), seeded by the one-shot `seed` service from
`data/intents.seed.jsonl` (≥ 10 exemplars per intent); injection exemplars in `injection_patterns`
from `data/injection.seed.jsonl`; benign look-alikes in `benign_patterns` from
`data/benign.seed.jsonl`. A message is refused as an injection when its top-1 score against
`injection_patterns` exceeds the nearer of `intents` and `benign_patterns` by ≥ `INJECTION_MARGIN`. Worker starts only after `seed` completes successfully; both fail
loud if the model or collection is missing. `data/intents.eval.jsonl` (≥ 3 per intent + ≥ 5
off-topic) is never loaded into Qdrant. Top-1 score < `ROUTE_THRESHOLD` ⇒ `general`.
`ROUTE_THRESHOLD` and `INJECTION_MARGIN` are set by leave-one-out over the seed files (max-accuracy
cut), recorded in ADR 0004; changing either requires an ADR update. `data/guard.holdout.jsonl` and
`data/guard.eval.jsonl` are never loaded into Qdrant.

## 9 · Inference and prompts

One OpenAI-compatible client at `LLM_BASE_URL` (inside the compose network). `MOCK_LLM=true` selects
the deterministic mock: output `"[mock] intent=<intent> turns=<n> last=<first 20 chars of previous
user turn or ->"`, split on spaces into tokens, tagged `"engine":"mock"`, WARNING logged at startup.
User text only in a delimited data field of the user message, never in the system prompt. Model
output is scrubbed before it is checkpointed, streamed, logged or traced.

## 10 · Human approval gate

A refund request proposed by the billing node is not applied until the thread owner approves it via
`POST /approve`. Pending approvals expire after `APPROVAL_TTL_S`, checked when `/approve` is
accepted. A new chat on the thread while a refund is pending abandons it (audited as
`refund_abandoned`); a later `/approve` for it returns 404. A refund is applied at most once per
originating message: the marker, the `refunds` stream entry and the `refund_applied` audit entry
are written together or not at all (arguments and key types are checked before the first write), the
marker holds the id of its `refunds` entry, a marker without that entry is an error rather than a
success, and the marker never expires. Applying means appending a
request to the Redis stream `refunds`; no payment processor is called. Proposals, decisions and
applications are written to the Redis stream `audit:events`.

## 11 · Config

Every key has a default in `.env.example`; config comes from the environment.
