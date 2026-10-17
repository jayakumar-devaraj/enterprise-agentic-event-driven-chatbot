# Low-level design

Companion to [`HLD.md`](HLD.md). Wire contracts are normative in `SPEC.md`; this file explains how the
pieces use them. Planned design as of 2026-10-17 10:55 CDT.

## 1 · Module map

| Module | Owns |
|---|---|
| `chat_common.schemas` | Pydantic v2 models: `ChatRequest`, `ChatAccepted`, `Envelope`, `TokenEvent`, `DoneEvent`, `ErrorEvent`, `ApprovalRequiredEvent`, `ApprovalRequest` |
| `chat_common.settings` | `Settings` (pydantic-settings, env); dedupe-lease formula validator |
| `chat_common.scrubber` | Presidio analyzer/anonymizer; `scrub()` sync, `ascrub()` via `asyncio.to_thread` |
| `chat_common.logjson` | stdlib `logging` JSON formatter carrying `trace_id`, `thread_id`, `message_id` |
| `chat_common.audit` | `audit:events` writer and reader |
| `gateway.app` | FastAPI app, routes, exception → status mapping |
| `gateway.auth` | HTTP Basic verification (scrypt), thread ownership |
| `gateway.ratelimit` | Per-thread fixed-window counter |
| `gateway.producer` | confluent-kafka asyncio producer wrapper |
| `gateway.sse` | Redis stream → SSE relay |
| `worker.consumer` | Poll loop, dedupe lease, retries, DLQ, commit |
| `worker.graph` | LangGraph `StateGraph` definition + checkpointer |
| `worker.router` | Embedding + Qdrant top-1 + thresholds |
| `worker.llm` | OpenAI-compatible client and deterministic mock; prompt assembly |
| `worker.guards` | L4 injection check, L5 output guard |
| `worker.approval` | Refund proposal, interrupt/resume, apply |
| `seed.main` / `seed.calibrate` | Load collections; leave-one-out threshold calibration |
| `frontend/src/api.ts` | `postChat`, `postApprove`, `streamMessage` (fetch + SSE parser, Basic header, `Last-Event-ID` resume) |
| `frontend/src/useChat.ts` | Chat state machine hook (see §11) |
| `frontend/src/ChatWindow.tsx` | Message list, incremental token rendering, input box, error banner, approve/reject panel |
| `frontend/src/Login.tsx` | Username/password form; credentials held in React state only |

## 2 · Sequence: chat turn (happy path)

```mermaid
sequenceDiagram
  autonumber
  participant C as React chat window
  participant G as Gateway
  participant R as Redis
  participant K as Kafka
  participant W as Worker
  participant Q as Qdrant
  participant L as LLM (Ollama/mock)
  C->>G: POST /chat {message, thread_id, message_id} + Basic auth
  G->>G: verify credentials (401), validate (422/413)
  G->>R: SET thread_owner NX / GET (403 if other user)
  G->>R: INCR ratelimit:{thread}:{minute} (429)
  G->>R: SET accepted:{message_id} NX (repeat ⇒ same 202, no produce)
  G->>G: ascrub(message) in thread pool
  G->>K: produce Envelope key=thread_id (503 on failure)
  G-->>C: 202 ChatAccepted {stream_url}
  C->>G: GET /stream/{thread}/{msg} (Last-Event-ID optional)
  W->>K: poll
  W->>R: SET dedupe:{message_id} processing NX EX lease
  W->>Q: embed(query prefix) → gRPC search injection_patterns, intents
  W->>L: chat.completions (system fixed, user text in delimited field)
  loop sentence-buffered chunks
    W->>W: L5 scrub chunk
    W->>R: XADD tokens:{thread}:{msg} TokenEvent{seq}
  end
  W->>R: checkpoint (scrubbed turn), XADD DoneEvent, SET dedupe done EX ttl
  W->>K: commit offset
  G->>R: XREAD BLOCK from 0 / Last-Event-ID
  G-->>C: SSE id=<entry id> data={seq, token} … event done ⇒ close
```

## 3 · Sequence: failure path

```mermaid
sequenceDiagram
  participant W as Worker
  participant D as Dependency (Qdrant/LLM/Redis)
  participant R as Redis stream
  participant K as Kafka
  W->>D: call (timeout)
  D--xW: timeout / connection error / 5xx
  W->>W: retry ≤ MAX_RETRIES, backoff ≤ RETRY_BACKOFF_MAX_S
  W->>R: XADD ErrorEvent{code: dependency_unavailable}
  W->>K: produce record to chat-requests.dlq
  W->>R: DEL dedupe:{message_id}
  W->>K: commit offset
  Note over W,K: Malformed envelope: DLQ + commit immediately, no retry
```

## 4 · Sequence: human approval gate

```mermaid
sequenceDiagram
  participant C as Client (thread owner)
  participant G as Gateway
  participant K as Kafka
  participant W as Worker
  participant R as Redis
  W->>W: billing node detects refund request (deterministic rule on scrubbed text)
  W->>W: interrupt() — graph state checkpointed, nothing applied
  W->>R: SET pending_approval:{thread}:{msg} EX APPROVAL_TTL_S; audit refund_proposed
  W->>R: XADD ApprovalRequiredEvent ⇒ stream closes
  C->>G: POST /approve/{thread}/{msg} {message_id: new, approved}
  G->>R: owner check (403), pending exists (404 if expired/unknown)
  G->>K: produce Envelope{kind: approval, ref_message_id, approved}
  G-->>C: 202 ChatAccepted {stream_url for new message_id}
  W->>W: resume graph with decision
  alt approved
    W->>R: XADD refunds (idempotent on ref_message_id); audit refund_applied
  else rejected
    W->>R: audit refund_rejected
  end
  W->>R: DEL pending; XADD tokens + DoneEvent on new stream
```

The refund decision is made by a deterministic rule, not by model output, so an injected prompt
cannot trigger or approve it. The apply step is idempotent on `ref_message_id` (Redis `SET NX`
marker before XADD), so a redelivered approval never applies twice.

## 5 · LangGraph state machine

```mermaid
stateDiagram-v2
  [*] --> route
  route --> refuse: injection score ≥ INJECTION_THRESHOLD
  route --> billing: intent=billing
  route --> tech_support: intent=tech_support
  route --> general: score < ROUTE_THRESHOLD or intent=general
  billing --> await_approval: refund requested
  await_approval --> apply_refund: approved
  await_approval --> respond: rejected
  apply_refund --> respond
  billing --> respond
  tech_support --> respond
  general --> respond
  refuse --> respond
  respond --> [*]
```

State (`TypedDict`): `thread_id`, `message_id`, `user_id`, `turns: list[{role, text}]` (scrubbed),
`intent`, `route_score`, `injection_score`, `refund: {status, ref_message_id} | None`.

## 6 · Redis key schema

| Key | Type | Written by | TTL / bound | Purpose |
|---|---|---|---|---|
| `tokens:{thread_id}:{message_id}` | Stream | worker | `MAXLEN ~ STREAM_MAXLEN`, `EXPIRE STREAM_TTL_S` | Token/done/error/approval events |
| `dedupe:{message_id}` | String `processing`/`done` | worker | `DEDUPE_LEASE_S` / `DEDUPE_TTL_S` | At-least-once → effectively-once |
| `accepted:{message_id}` | String (thread_id) | gateway | `DEDUPE_TTL_S` | Idempotent 202 |
| `ratelimit:{thread_id}:{epoch_min}` | Counter | gateway | 60 s | 429 |
| `thread_owner:{thread_id}` | String (user) | gateway | `CHECKPOINT_TTL_MIN` | Access binding |
| `pending_approval:{thread_id}:{message_id}` | String (user) | worker | `APPROVAL_TTL_S` | Approval window |
| `refund_applied:{message_id}` | String | worker | `DEDUPE_TTL_S` | Apply idempotency |
| `refunds` | Stream | worker | none | The side effect being gated |
| `audit:events` | Stream | gateway, worker | `MAXLEN ~ 100000` | Durable, queryable runtime audit trail |
| checkpointer keys | JSON (library-managed) | worker | `CHECKPOINT_TTL_MIN` | Conversation memory per `thread_id` |

## 7 · Kafka

| Topic | Partitions | Key | Value |
|---|---|---|---|
| `chat-requests` | `KAFKA_PARTITIONS` | `thread_id` | `Envelope` JSON |
| `chat-requests.dlq` | 1 | `thread_id` (or null if unparseable) | `{reason, error, original (base64 if not JSON)}` |

`Envelope = {message_id, thread_id, user_id, text, ts, trace_id, kind: "chat"|"approval",
ref_message_id?, approved?}`. Trace context travels only in `trace_id`: the async producer does not
support per-message headers (spike b, ADR 0003). Consumer: `enable.auto.commit=false`; commit after the durable outcome.

## 8 · API status matrix

| Endpoint | 2xx | 401 | 403 | 404 | 413 | 422 | 429 | 503 |
|---|---|---|---|---|---|---|---|---|
| `POST /chat` | 202 `ChatAccepted` | bad/missing creds | thread owned by another user | — | message > `MAX_MESSAGE_CHARS` | invalid body / `thread_id` / reused `message_id` on another thread | per-thread rate | Kafka or Redis down |
| `GET /stream/{t}/{m}` | 200 SSE | bad/missing creds | not owner | — | — | invalid ids | — | Redis down |
| `POST /approve/{t}/{m}` | 202 `ChatAccepted` | bad/missing creds | not owner | no pending approval | — | invalid body | — | Kafka or Redis down |
| `GET /healthz` | 200 | — | — | — | — | — | — | dependency down |

Infra faults never map to 4xx; caller faults never map to 5xx.

## 9 · Prompt assembly (L4 structural defence)

```
system: <fixed constant per intent node, byte-identical every call>
user:   <user_message>{scrubbed text, delimiters escaped}</user_message>
        <history>{last turns, scrubbed}</history>
```

The system prompt instructs the model to treat everything inside the tags as data. A7 asserts the
system prompt is byte-identical to the baseline and that injected text appears only inside
`<user_message>`.

## 10 · Output guard and streaming

The worker buffers model tokens until a sentence boundary (or 200 chars), runs the scrubber on that
chunk off-loop, checks for a system-prompt echo, then XADDs the chunk's tokens. This trades a little
time-to-first-token for the guarantee that unscrubbed output never reaches the stream, checkpoint,
logs or traces. An entity split across a chunk boundary is a known residual risk.

## 11 · React chat window

Vite + React 19 + TypeScript 6 single-page app in `frontend/`, served on :5173 in development and as a
compose service built from `node:24.22.0-slim`. The gateway's `CORS_ORIGINS` allowlists its origin.

```mermaid
stateDiagram-v2
  [*] --> signed_out
  signed_out --> idle: credentials entered (kept in memory)
  idle --> sending: submit (new message_id = crypto.randomUUID())
  sending --> streaming: 202 ChatAccepted
  sending --> error: 401 / 403 / 413 / 422 / 429 / 503
  streaming --> streaming: token event (append to the assistant bubble)
  streaming --> idle: done event
  streaming --> error: error event / network failure after one resume attempt
  streaming --> awaiting_approval: approval_required event
  awaiting_approval --> sending: Approve / Reject (POST /approve)
  error --> idle: dismiss
```

- **Streaming:** `fetch(stream_url, {headers: {Authorization, Last-Event-ID?}})`, read
  `response.body` with a `TextDecoder`, split on blank lines, parse `id:` / `event:` / `data:`. On
  a dropped connection it retries once with the last seen `id` as `Last-Event-ID`; SPEC §6 makes
  this gap- and duplicate-free.
- **Incremental render:** tokens append to the current assistant message as they arrive; `seq` is
  checked to be increasing and duplicates are ignored.
- **Error state:** HTTP status or `ErrorEvent.code` maps to a readable banner; 401 returns to
  sign-in.
- **Approval:** `ApprovalRequiredEvent` shows Approve / Reject buttons; the click POSTs `/approve`
  with a fresh `message_id` and streams the follow-up message.
- **Tests (A8, Vitest + React Testing Library):** a stubbed `fetch` streams events; asserts tokens
  appear incrementally, the error banner renders on `ErrorEvent`, and the approval panel appears on
  `approval_required`.

## 12 · Qdrant transport

The worker uses `AsyncQdrantClient(host, grpc_port=6334, prefer_grpc=True)` for search (lead
choice, ADR 0006). The seed service and compose healthcheck keep REST on :6333. Both ports are
published only inside the compose network.
