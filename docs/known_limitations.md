# Known limitations

Appended whenever scope is cut, a shortcut is taken, or something is left unverified.

- **Cut at Step B (time):** Tier 3 (Caddy TLS/HTTP2, A11 latency), A10 horizontal-scale test, React
  client + A8, DeepEval answer-quality eval + A9 real Ollama round trip, service container images +
  full-compose end-to-end + A4 Qdrant-stopped case. Recorded in `docs/master_plan.md`.
- **Access control over plain HTTP:** HTTP Basic credentials travel in cleartext until a TLS proxy
  exists (Tier 3, cut).
- **LangFuse Cloud egress:** scrubbed prompts and outputs leave the machine to US LangFuse Cloud;
  runtime is no longer fully offline.
