"""Redis checkpointer whose writes stop once this attempt's dedupe lease is lost (ADR 0009)."""

from typing import Any

from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from worker.graph import current_writer


class FencedRedisSaver(AsyncRedisSaver):
    """AsyncRedisSaver that checks the processing attempt's lease before every write.

    A stale worker builds its checkpoints on the history it loaded before it stalled. Written after
    another worker answered later turns, such a checkpoint becomes the thread's latest and drops
    them. The check is not atomic with the saver's write (ADR 0009). Writes made outside a
    processing attempt (no current writer) are not fenced.
    """

    async def _fence(self) -> None:
        writer = current_writer.get(None)
        if writer is not None:
            await writer.check_lease()

    async def aput(self, *args: Any, **kwargs: Any) -> Any:
        """Store a checkpoint, if the lease is still held."""
        await self._fence()
        return await super().aput(*args, **kwargs)

    async def aput_writes(self, *args: Any, **kwargs: Any) -> None:
        """Store a task's pending writes, if the lease is still held."""
        await self._fence()
        await super().aput_writes(*args, **kwargs)
