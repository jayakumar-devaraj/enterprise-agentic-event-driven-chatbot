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

## 2 · HTTP API (gateway :8000, HTTP Basic on every endpoint except `/healthz`)

| Endpoint | Success | Errors |
|---|---|---|
| `POST /chat` | 202 `ChatAccepted`, only after the broker acknowledged the record; a repeated `message_id` with the same request returns the same 202 body, and one whose earlier attempt was never acknowledged is produced again | 401 credentials · 403 thread owned by another user · 413 message over `MAX_MESSAGE_CHARS` · 422 invalid body, `thread_id`, or `message_id` reused for a different request (other thread, text or decision) · 429 per-thread rate limit · 503 Kafka or Redis unavailable |
| `GET /stream/{thread_id}/{message_id}` | 200 `text/event-stream` | 401 · 403 · 422 · 503 |
| `POST /approve/{thread_id}/{message_id}` | 202 `ChatAccepted` for the new message | 401 · 403 · 404 no pending approval · 422 · 503 |
| `GET /healthz` | 200 | 503 dependency down |

4xx = caller fault, 5xx = infrastructure fault, at every boundary. CORS allowlist from
`CORS_ORIGINS`, no wildcard.

## 3 · Access control

The first authenticated `POST /chat` on a `thread_id` binds it to that user
(`thread_owner:{thread_id}`); every later chat, stream or approve call on that thread by another
user returns 403. The binding never expires, so it outlives every piece of thread data.

## 4 · Kafka

Topics `chat-requests` (partitions = `KAFKA_PARTITIONS`, key = `thread_id`) and
`chat-requests.dlq`. Per-thread order comes from the key; worker replicas ≤ `KAFKA_PARTITIONS`.
Changing the partition count remaps keys and is an ADR decision, never a runtime tweak. Offsets are
committed manually and only after the outcome is durable (stream event written, or DLQ written).

## 5 · Worker idempotency

`SET dedupe:{message_id} processing NX EX DEDUPE_LEASE_S`. On success set `done` with `DEDUPE_TTL_S`.
On terminal failure delete the key. `done` ⇒ skip and commit; `processing` ⇒ do not commit; the lease
expires and the message is redelivered. Settings validation fails startup unless
`DEDUPE_LEASE_S > INFERENCE_TIMEOUT_S × (MAX_RETRIES + 1) + RETRY_BACKOFF_MAX_S × MAX_RETRIES`.

## 6 · Token stream

One Redis Stream per message, `tokens:{thread_id}:{message_id}`; `seq` starts at 0; `MAXLEN` and TTL
from config. SSE `id:` is the Redis entry id; the payload carries `seq`. Read from `0`, or from
`Last-Event-ID` (exclusive) when sent. Close after `done`, `error` or `approval_required`. A client
disconnect stops the relay without error logs.

## 7 · Failure path

Infra fault (Qdrant / LLM / Redis) → bounded retries with backoff → `ErrorEvent{code:
"dependency_unavailable"}` to the stream, record to the DLQ, then commit. Malformed envelope → DLQ
immediately, no retry.

## 8 · Routing and seeding

Qdrant collection `intents` (384-dim, cosine), seeded by the one-shot `seed` service from
`data/intents.seed.jsonl` (≥ 10 exemplars per intent); injection exemplars in `injection_patterns`
from `data/injection.seed.jsonl`. Worker starts only after `seed` completes successfully; both fail
loud if the model or collection is missing. `data/intents.eval.jsonl` (≥ 3 per intent + ≥ 5
off-topic) is never loaded into Qdrant. Top-1 score < `ROUTE_THRESHOLD` ⇒ `general`.
`ROUTE_THRESHOLD` is set once by leave-one-out over the seed file (max-accuracy cut), recorded in an
ADR; changing it requires an ADR update.

## 9 · Inference and prompts

One OpenAI-compatible client at `LLM_BASE_URL` (inside the compose network). `MOCK_LLM=true` selects
the deterministic mock: output `"[mock] intent=<intent> turns=<n> last=<first 20 chars of previous
user turn or ->"`, split on spaces into tokens, tagged `"engine":"mock"`, WARNING logged at startup.
User text only in a delimited data field of the user message, never in the system prompt. Model
output is scrubbed before it is checkpointed, streamed, logged or traced.

## 10 · Human approval gate

A refund request proposed by the billing node is not applied until the thread owner approves it via
`POST /approve`. Pending approvals expire after `APPROVAL_TTL_S`. A refund is applied at most once per
originating message: the marker, the `refunds` stream entry and the `refund_applied` audit entry
are written together or not at all, and the marker never expires. Applying means appending a
request to the Redis stream `refunds`; no payment processor is called. Proposals, decisions and
applications are written to the Redis stream `audit:events`.

## 11 · Config

Every key has a default in `.env.example`; config comes from the environment.
