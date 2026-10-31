"""DEBUG ONLY (branch debug/rebalance-diag): per-thread event log in Redis that survives SIGKILL."""

import json
import os
import socket
import time

HOST = socket.gethostname()


async def diag(redis, thread_id, event, **fields):
    if not os.environ.get("WORKER_DIAG"):
        return
    rec = {"t": round(time.time(), 3), "host": HOST, "event": event, **fields}
    try:
        await redis.rpush(f"diag:{thread_id}", json.dumps(rec, default=str))
    except Exception:
        pass
