# A9 — real inference round trip (Ollama, MOCK_LLM=false)

2026-10-17 ~19:00 CDT. `MOCK_LLM=false docker compose -f deploy/compose.yaml --profile llm up -d --wait`
(`ollama/ollama:0.40.6` with `llama3.2:1b` baked in at build; worker env `MOCK_LLM=false`).

Manual round trip through the containerized gateway ("My Wi-Fi is very slow in the evening. What
should I try first?"): `POST 202`, stream complete after 21 s, 108 events ending
`{'type': 'done', 'seq': 107}`; reply began
`<user_message>My Wi-Fi is very slow … first?</user_message> In cases where you're experiencing issues
with your internet speed, … First, try restarting your router. …`

Automated: `uv run pytest -m llm -v`
```
tests/load/test_ollama_a9.py::test_a9_real_llm_round_trip PASSED         [100%]
====================== 1 passed, 86 deselected in 52.34s ======================
```

Found on the way:
- The first real round trip returned an empty stream: the gateway's Redis client timed out its own
  `XREAD BLOCK 5000` ("Timeout reading from redis:6379") because the first token took longer than
  one block window. Fixed by giving the gateway client `socket_timeout = block + 5 s`
  (`gateway.app.make_redis`); regression test `tests/integration/test_stream_slow_first_event.py`
  (the old client construction fails the same scenario with `TimeoutError`).
- `deploy/ollama/Dockerfile` used `kill %1`, which the image's `/bin/sh` cannot do; it now kills the
  server by PID.
- The 1B model echoes the delimited `<user_message>` block before answering (logged limitation).
