# SPEC — contracts

Normative data contract. Anything not written here is not guaranteed. Source: the brief's
"Contracts" section (`docs/prompts/problem-statement.md`) plus the deviations approved in
`docs/adr/0006-approved-deviations.md`. Rationale lives in `docs/adr/`; design in `docs/architecture/`.

## 1 · Schemas (Pydantic v2, defined once in `packages/common`)

| Model | Fields |
|---|---|
| `ChatRequest` | `message: str`, `thread_id: str`, `message_id: UUID` |
| `ChatAccepted` | `message_id`, `thread_id`, `stream_url` |
| `Envelope` (Kafka value) | `message_id`, `thread_id`, `user_id`, `text` (scrubbed), `ts`, `trace_id`, `kind: "chat" \| "approval"`, `ref_message_id?`, `approved?`, `tenant_id?` |
| `TokenEvent` | `seq`, `token` |
| `DoneEvent` | `seq` |
| `ErrorEvent` | `seq`, `code`, `detail` |
| `ApprovalRequiredEvent` | `seq`, `action: "refund_request"`, `detail` |
| `StatusEvent` (not terminal) | `seq`, `detail` |
| `CitationsEvent` (not terminal) | `seq`, `citations: [{doc_key, page_key}]` (locations, never document text) |
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
binding. The worker checks ownership too, on every envelope it processes: a missing binding is
restored to the user the thread's history belongs to (or the envelope's user for a new thread), and an
envelope from anyone else gets `ErrorEvent{thread_owner_mismatch}` and changes nothing. A replay after a
Redis restore therefore re-binds every thread it rebuilds (§12).

**Tenant.** `USER_TENANTS` (`user:tenant,...`, a `TenantId` of `[A-Za-z0-9_-]{1,64}`) maps an
authenticated username to the document tenant its requests run under. It is parsed at gateway
startup like `AUTH_USERS`; a malformed entry, a duplicate or a user missing from `AUTH_USERS` fails
startup. The gateway sets `Envelope.tenant_id` from the username alone and never from a request body
(`ChatRequest` forbids unknown fields). A user with no entry has `tenant_id` unset, and the field is
left out of the Kafka value then, so a worker that predates it still accepts the record. A message
routed to `document_rag` that carries no tenant has nothing to search, so the worker answers it as
`general` (§8, §13). Deploy
workers before gateways: a worker without the field rejects an envelope that has it
(`extra="forbid"`) as malformed.

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

`status` and `citations` are progress, not terminal. A reply that searches documents sends one
`status` before the search and one `citations` after its last token and before `done`. A client reads
past every type it does not know. A `status` is not reply output: a transient fault after it is still
retried (§7), and the retried attempt does not send it again. A stream whose last event is not
terminal is a partial attempt (§6, below), whatever type that event has.

A reply never exceeds `LLM_MAX_TOKENS` tokens, one redaction token and a terminal event, and settings
validation fails startup unless `STREAM_MAXLEN > LLM_MAX_TOKENS + 2`, so a stream is never trimmed and
a resume never meets a gap. A document reply adds a `status`, the `citations` and a fixed caveat of
at most 20 tokens to `RAG_MAX_TOKENS` tokens, so startup also requires
`STREAM_MAXLEN > RAG_MAX_TOKENS + 2 + 22`. A stream that no longer exists, for a message that finished (dedupe
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
immediately, no retry. Its bytes skipped the gateway's scrubber, so neither the DLQ record nor the log
keeps any of them: the record holds their SHA-256, their length and the validation error types
with `Envelope` field locations (other location parts become `?`). A tracing backend that is down or unreachable (Jaeger via OTLP, LangFuse) is
not a dependency: span export runs in the background and LangFuse calls are caught. Both log the
failure and drop the trace data, and requests complete as usual.

DLQ records are inspected and replayed with `python -m worker.dlq list | replay --offset N... | --all`
(worker image or `uv run`). Replay re-produces a record's envelope keyed by its thread. If the failed
attempt's stream ends in `dependency_unavailable`, replay deletes it first, so the worker processes
the message instead of recording the old outcome again. A replayed message joins its thread's order
when it is replayed, not where it was first sent. Replay is idempotent (dedupe skips a completed
message). Malformed records have no envelope and are not replayable.

## 8 · Routing and seeding

Qdrant collection `intents` (384-dim, cosine), seeded by the one-shot `seed` service from
`data/intents.seed.jsonl` (≥ 10 exemplars per intent); injection exemplars in `injection_patterns`
from `data/injection.seed.jsonl`; benign look-alikes in `benign_patterns` from
`data/benign.seed.jsonl`. A message is refused as an injection when its top-1 score against
`injection_patterns` exceeds the nearer of `intents` and `benign_patterns` by ≥ `INJECTION_MARGIN`. Worker starts only after `seed` completes successfully; both fail
loud if the model or collection is missing. `data/intents.eval.jsonl` (≥ 3 per intent + ≥ 5
off-topic) is never loaded into Qdrant. Top-1 score < `ROUTE_THRESHOLD` ⇒ `general`.
`ROUTE_THRESHOLD` and `INJECTION_MARGIN` are set by leave-one-out over the seed files (max-accuracy
cut), recorded in ADR 0004. Intents are `billing`, `tech_support`, `general` and `document_rag`
(a question about the user's own documents, answered per §13); changing either requires an ADR update. `data/guard.holdout.jsonl` and
`data/guard.eval.jsonl` are never loaded into Qdrant.

## 9 · Inference and prompts

One OpenAI-compatible client at `LLM_BASE_URL` (inside the compose network). `MOCK_LLM=true` selects
the deterministic mock: output `"[mock] intent=<intent> turns=<n> last=<first 20 chars of previous
user turn or ->"`, split on spaces into tokens, tagged `"engine":"mock"`, WARNING logged at startup.
User text only in a delimited data field of the user message, never in the system prompt. Model
output is scrubbed before it is checkpointed, streamed, logged or traced.

## 10 · Human approval gate

A refund request proposed by the billing node is not applied until the thread owner approves it via
`POST /approve`. Pending approvals expire `APPROVAL_TTL_S` after the proposal: a decision must be
accepted by the gateway within that window. The gateway checks it at `/approve` (404 after it), and
the worker checks it again before applying, against the acceptance time the envelope carries and the
proposal time in the checkpoint (`proposed_at`). A decision accepted too late gets
`ErrorEvent{approval_expired}`, is audited as `refund_expired`, and applies nothing. The worker also
refuses a decision whose `user_id` is not the thread owner's, which is the requester's (`thread_owner_mismatch`, §3). Both times come from
the container clocks, which share the host clock under compose. When two different decisions are
both accepted, the first one processed settles the refund and the second gets `approval_not_pending`.
A new chat on the thread while a refund is pending abandons it (audited as
`refund_abandoned`); a later `/approve` for it returns 404. A refund is applied at most once per
originating message: the marker, the `refunds` stream entry and the `refund_applied` audit entry
are written together or not at all (arguments and key types are checked before the first write), the
marker holds the id of its `refunds` entry, a marker without that entry is an error rather than a
success, and the marker never expires. Applying means appending a
request to the Redis stream `refunds`; no payment processor is called. Proposals, decisions and
applications are written to the Redis stream `audit:events`.

## 11 · Config

Every key has a default in `.env.example`; config comes from the environment.

## 12 · Durability and recovery

Failure model: one host. Redis writes an AOF with `appendfsync everysec` and Kafka a log with
replication factor 1, each on a named volume. Objectives and measurements are in ADR 0008.

| | Guarantee |
|---|---|
| RPO, process crash or container replacement | 0 for Redis and Kafka |
| RPO, host or OS crash | Redis ≤ 1 s of acknowledged writes. Kafka bounded by the OS page-cache flush |
| RTO | Redis 4.3 s, Kafka 16.5 s from SIGKILL to healthy (measured on the development machine, ADR 0008) |
| Retention | `chat-requests` records `KAFKA_RETENTION_MS` (+ one `KAFKA_SEGMENT_MS`). Dedupe markers `DEDUPE_TTL_S` (§5). Token streams `STREAM_TTL_S`. Checkpoints `CHECKPOINT_TTL_MIN`, refreshed on read. Owner bindings and refund markers never expire. `audit:events` capped by `MAXLEN` |
| Backup | Redis: tar of the stopped `redis-data` volume (README). Kafka: none |

**Restoring a Redis backup.** The backup must be younger than `KAFKA_RETENTION_MS`. Stop the
gateways and the workers, restore the volume, rewind the worker group to the earliest retained
record, start the workers, and start the gateways once the group has no lag (README). Messages
finished before the backup find their `done` marker and are skipped. Every message processed after
it is processed again, in per-thread order, exactly once relative to the restored state: its turns,
token stream, pending approval, refund receipt (`refunds` entry and marker) and thread binding (§3)
are rebuilt. Not rebuilt: gateway-side records written after the backup (`accepted:`/`delivered:`
markers, so a client retry of such a message is produced again and deduplicated, `*_accepted` audit
entries, rate-limit counters). A real model's regenerated reply can differ from the one the client
already saw. Gateways stay stopped until the replay finishes, because a thread whose binding was lost
could otherwise be claimed before its replay restores it (the replay would then be refused,
`thread_owner_mismatch`). Losing the Kafka volume loses the requests it held that no worker had
finished, and their 202 acceptances.

## 13 · Document questions (RAG service)

A message routed to `document_rag` (§8) is answered from the caller's tenant documents. The worker
calls the RAG service's `POST /v1/retrieve`; the contract is
[`docs/contracts/rag-openapi-v1.json`](docs/contracts/rag-openapi-v1.json), and a unit test fails if
the worker's response models drift from it. The service is a separate container at `RAG_BASE_URL`,
used at a pinned image tag and never as source. A message routed to `document_rag` from a user with no
tenant has nothing to search: the worker answers it as `general`, with no status, search or citations.

**Call.** Only the scrubbed, standalone question is sent, with `Authorization: Bearer <tenant
token>` (`RAG_TENANT_TOKENS`, `token:tenant,...`; or, when `RAG_TENANT_CLIENTS` and `RAG_TOKEN_URL` are set instead, the access token from that tenant's OAuth client-credentials client, never both and never a fallback between them), `traceparent`, `X-Request-ID` (the trace id) and
`X-Timeout-Ms`. The tenant is `Envelope.tenant_id` (§3); the body carries none. The whole call has
one deadline, `RAG_TIMEOUT_S`. Every document turn first streams a `status` event.

**Errors.** A connection error, 503 or 504 is a dependency fault: bounded retries while no reply
has started (§7), then `dependency_unavailable` and the DLQ. Anything else ends the turn at once
with a fixed `ErrorEvent`, not retried and not sent to the DLQ: `rag_tenant_unknown` (no token for the tenant), `rag_unauthorized` (401), `rag_bad_request`
(422), `rag_error` (500, any other status, or a body off-contract).

**Answer.** When the service reports `insufficient`, the reply is a fixed "not found in your
documents" sentence and no model is called. Otherwise the sections are placed in an escaped
`<documents>` data block, never in the system prompt (§9), and the reply streams through the output
guard. Each sentence is checked before it is scrubbed: a sentence with a number that occurs in no
section (or the question) is dropped, and one fixed caveat follows the last sentence. After the last
token the worker sends `citations`, then `done`.

A model that returns no text is a dependency fault (§7), not an empty answer: it is retried while
no reply has started, and no `citations` event is sent for it.

**Data.** Section text is never checkpointed, streamed, logged, traced or sent to the DLQ. The
checkpoint keeps `doc_key`, `page_id`, `page_key` and `score` per section, reset every turn. The
scrubbed reply is stored in `turns` like any other. Raw section text does reach the chat model
provider (ADR 0010).
