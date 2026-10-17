# Task 8 — LangGraph graph

**Purpose:** StateGraph with routing as the first node (injection check → refusal node; intent top-1
with threshold); `billing` / `tech_support` / `general` nodes calling one OpenAI-compatible client or
the deterministic mock; user text in a delimited data field, fixed system prompt; L5 output guard
(scrub, token cap, echo check); Redis checkpointer per `thread_id` with TTL.
**Verify:** A6 (second turn shows `turns=2` + first-turn prefix) and A7 (injection text only inside the
delimited field, system prompt byte-identical to baseline) tests.
