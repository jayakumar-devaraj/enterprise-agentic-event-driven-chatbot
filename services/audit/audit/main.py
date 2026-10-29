"""Run the audit exporter until SIGTERM/SIGINT: `python -m audit.main`.

AUDIT_DATABASE_URL is the audit_writer role's URL; REDIS_URL the stream's Redis. A database or Redis
fault is logged and retried; nothing is acknowledged until its rows are committed.
"""

import asyncio
import logging
import os
import signal
import socket

import psycopg
import redis.asyncio as aioredis
from chat_common.logjson import configure_logging
from redis.exceptions import RedisError

from audit.export import Exporter

log = logging.getLogger("audit.main")
RETRY_S = 2.0
BLOCK_MS = 5000


async def run(stop: asyncio.Event) -> None:
    """Export batches until `stop` is set."""
    redis = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    exporter = Exporter(redis, os.environ["AUDIT_DATABASE_URL"], consumer=socket.gethostname())
    try:
        await exporter.ensure_group()
        while not stop.is_set():
            try:
                done = await exporter.export_once(block_ms=BLOCK_MS)
                if done:
                    log.info("audit_exported", extra={"entries": done, "lag": await exporter.lag()})
            except (psycopg.Error, RedisError, OSError) as exc:
                log.error("audit_export_failed", extra={"error": type(exc).__name__})
                await asyncio.sleep(RETRY_S)
    finally:
        await redis.aclose()


def main() -> None:
    """Entry point."""
    configure_logging()
    stop = asyncio.Event()

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows: Ctrl+C raises KeyboardInterrupt instead
                pass
        await run(stop)

    asyncio.run(runner(), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    main()
