# A10 — horizontal scale (2 gateways + 2 workers)

2026-10-17 ~18:40 CDT, `docker compose -f deploy/compose.yaml up -d --wait --scale worker=2 --scale gateway=2`
(gateways on 127.0.0.1:8003 and :8002).

`uv run pytest -m scale -v` — 6 threads × 3 turns posted back to back, alternating gateway replicas;
each stream read on the other replica:
```
tests/load/test_scale_a10.py::test_a10_scaled_order_no_duplicates_cross_replica PASSED [100%]
====================== 1 passed, 84 deselected in 30.32s ======================
```
Consumer group `chat-workers` after the run (both workers own partitions, no lag):
```
PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG CONSUMER-ID                                   HOST
2         3              3              0   rdkafka-7393ad44-6e64-417f-a970-f18b32bed0b9  /172.29.0.9
3         15             15             0   rdkafka-7393ad44-6e64-417f-a970-f18b32bed0b9  /172.29.0.9
0         3              3              0   rdkafka-582c837c-e644-4a12-9c80-00f1b27328e2  /172.29.0.4
1         16             16             0   rdkafka-582c837c-e644-4a12-9c80-00f1b27328e2  /172.29.0.4
```
First run failed on the test's own expected value (it sliced 20 characters from a shortened copy
of the message); the system output was already correct (`turns=2 last=turn 0 of thread 0 a`).
