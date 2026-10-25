# 0010 · Document questions through a separate RAG service

**Status:** Accepted 2026-10-25.

## Context
Users should be able to ask questions about their own documents. A separate service already owns
ingestion, tenant-isolated hybrid retrieval and a self-correcting retrieval loop (the RAG service,
its own repository, container and Postgres). The chatbot should reuse it, keep writing and
streaming the answer itself through its own model engine and output guard, and not copy retrieval
logic. A user is waiting on the call, so it is synchronous HTTP, not Kafka.

The chatbot had no tenant concept, its guarantees are about scrubbed text (PII never leaves the
gateway unscrubbed, nothing unscrubbed is streamed), and its worker retries only while nothing has
been streamed. Each of those needed a decision.

## Decision
- **Boundary.** The RAG service returns passages from `POST /v1/retrieve`; the worker writes the
  answer. The contract is pinned as `docs/contracts/rag-openapi-v1.json` and the service is used
  as a published image at a pinned tag, never as source. A unit test fails if the worker's response
  models drift from the contract. The only code copied is `numeric_groundedness` (`worker/numeric.py`),
  held to the original by golden cases generated from it.
- **Tenancy.** `USER_TENANTS` maps a username to a tenant. The gateway sets `Envelope.tenant_id`
  from the authenticated username only. The worker maps tenant to the service's bearer token from
  `RAG_TENANT_TOKENS`, written in the service's own `token:tenant` format so the same value feeds
  both. A message routed to `document_rag` from a user with no tenant is answered as `general`: the router
  cannot tell who has documents, and a false positive on ordinary text must not turn into an error
  (a durability test's plain messages hit exactly that). The field is left out of
  the Kafka value when unset, so an older worker accepts records from users without a tenant.
- **D1 · Raw document text reaches the chat model provider.** Until now the provider saw only
  scrubbed text. Retrieved sections are not scrubbed before the model, because scrubbing rewrites
  numbers and identifiers, which would break both the answer and the number check. This is accepted
  because the RAG service already sends the same text to its own model providers. The condition: the
  chatbot's model provider must be at least as trusted as the RAG service's. Section text still never
  enters the checkpoint, the stream, logs, traces or the DLQ. The scrubbed reply is stored in `turns`
  like any reply, and it can repeat document content.
- **Section text stays out of graph state.** Graph state is checkpointed after every node, so the
  sections travel from `rag_retrieve` to `rag_respond` in a per-call `Scratch` object held in a
  context variable. The checkpoint keeps `doc_key`, `page_id`, `page_key` and `score`. A retry
  restarts the graph from the search, so a lost `Scratch` is never read (tested).
- **D7 · Number check while streaming.** The service's own gate replaces a whole answer when any
  number is unsupported. Here sentences have already been streamed, so the check is per sentence,
  before the scrub (scrubbing rewrites numbers, and the sources are raw): an ungrounded sentence is
  dropped and one fixed caveat is added after the last sentence. The question counts as a source, so
  a figure the user typed is not an invention. Known weakness, shared with the original: a numbered
  list or "page 2" in an answer is dropped; the prompt forbids both.
- **Retries.** The consumer owns them. A connection error, 503 or 504 is a dependency fault: bounded
  retries, then `dependency_unavailable` and the DLQ. 401, 422, 500 and anything off-contract end the
  turn at once with a fixed error, not retried and not dead-lettered, because no retry changes them
  and a poison message would otherwise be redelivered forever. A model that returns no text is also a
  dependency fault: a real run showed status, citations and `done` with zero tokens.
- **The `status` event and the retry rule.** The first `status` event is `seq` 0, and the consumer
  blocked retries once `seq > 0`. Read literally, a 503 after `status` could never be retried. The
  rule is now "no reply output yet": `StreamWriter.reply_started` ignores status events, and a retried
  attempt does not repeat the note. Separately, the consumer decided a crashed attempt was finished
  when its last event was "not a token"; with `status` and `citations` that would have skipped the
  work, so it now tests `TERMINAL_TYPES`.
- **Time.** One deadline, `RAG_TIMEOUT_S` (default 20), is sent as `X-Timeout-Ms` minus 500 ms so the
  service's 504 can arrive first. `INFERENCE_TIMEOUT_S` already wraps a whole attempt, RAG call
  included, so the lease formula (SPEC §5) still bounds the worst case with no new term. The added
  check is that, with RAG on, `RAG_TIMEOUT_S < INFERENCE_TIMEOUT_S`, so the search cannot use the
  whole attempt. `STREAM_MAXLEN` must also hold a document reply (SPEC §6).
- **Routing.** A fourth intent, `document_rag`, recalibrated under the pre-registration in ADR 0004
  (all targets met). Routing is by similarity to exemplars, so a document question that names no
  document and is far from every exemplar goes to `general`.
- **Tracing.** `traceparent` and `X-Request-ID` (the trace id) are sent to the service. The service
  logs the request id; it does not read `traceparent`, so a trace is joined by id, not as one span tree.
- **Compose.** `rag-db`, `rag-api` and `rag-seed` sit behind the `rag` profile, so the default stack's
  memory is unchanged. The worker waits for `rag-api` only when the profile is on.

## Consequences
- Old frontends do not tolerate the new events: they treat any non-token event as the end of the
  stream. Status and citations are sent only on document turns of users who have a tenant, so with
  `USER_TENANTS` empty none are ever sent. Roll out workers, then the frontend, then set
  `USER_TENANTS` on the gateways. The gateway cannot filter the events for old clients without
  renumbering `seq`, which the resume logic relies on.
- Deploying a gateway with `USER_TENANTS` before the workers would make older workers dead-letter
  those users' records as malformed (`extra="forbid"`).
- A user with no tenant never sees a document-related error; a real document question from them gets a
  plain assistant answer, not "you have no documents".
- The first message of a thread that mentions no document is answered by the plain assistant even
  from a user with a tenant. Making tenant users always search documents would remove that, at the
  cost of a search on every message.
- The model provider now receives raw document text (D1). Switching providers needs that review.
- A document reply is stored in the thread's `turns` and can be shown to anyone the thread is shared
  with; threads are single-owner (SPEC §3).
- The pinned image tag must exist and be pullable before this stack can start with the `rag`
  profile; the repository's CI does not run the `rag` tests.
