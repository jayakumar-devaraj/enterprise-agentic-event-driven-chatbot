"""Redis checkpointer whose history only advances under this attempt's dedupe lease (ADR 0009)."""

from typing import Any

from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from worker.graph import current_writer
from worker.streams import head_key


class FencedRedisSaver(AsyncRedisSaver):
    """AsyncRedisSaver that fences writes by the processing attempt's lease and reads the head.

    A stale worker builds its checkpoints on the history it loaded before it stalled. Written after
    another worker answered later turns, such a checkpoint would become the thread's latest and drop
    them. Each write is preceded by a lease check, but the check is not atomic with the write, so a
    write can still land. What makes it harmless: every checkpoint written under a held lease moves
    `thread_head:{thread_id}` in one fenced script, and reading a thread's current state returns the
    head checkpoint, not the newest one. A stale write cannot move the head (LeaseLost), so it stays
    an orphan. Writes made outside a processing attempt (no current writer) are not fenced.
    """

    def _head_ttl_s(self) -> int:
        return int(self.ttl_config["default_ttl"] * 60) if self.ttl_config else 0

    async def _fence(self) -> None:
        writer = current_writer.get(None)
        if writer is not None:
            await writer.check_lease()

    async def aget_tuple(self, config: Any) -> Any:
        """The head checkpoint when no checkpoint is named; else as AsyncRedisSaver."""
        conf = config["configurable"]
        if conf.get("checkpoint_id") or conf.get("checkpoint_ns"):
            return await super().aget_tuple(config)
        key, ttl = head_key(conf["thread_id"]), self._head_ttl_s()
        head = await (self._redis.getex(key, ex=ttl) if ttl else self._redis.get(key))
        if head is None:  # a thread written before heads existed
            return await super().aget_tuple(config)
        head = head.decode() if isinstance(head, bytes) else head
        return await super().aget_tuple({**config, "configurable": {**conf, "checkpoint_id": head}})

    async def aput(self, *args: Any, **kwargs: Any) -> Any:
        """Store a checkpoint and move the head to it, if the lease is still held."""
        await self._fence()
        saved = await super().aput(*args, **kwargs)
        writer = current_writer.get(None)
        if writer is not None and not saved["configurable"].get("checkpoint_ns"):
            await writer.advance_head(saved["configurable"]["checkpoint_id"], self._head_ttl_s())
            from worker.diag import diag
            meta = args[2] if len(args) > 2 else kwargs.get("metadata", {})
            await diag(writer._redis, writer.thread_id, "head", mid=str(writer.message_id),
                       cid=saved["configurable"]["checkpoint_id"], step=meta.get("step"),
                       parent=(args[0] if args else kwargs.get("config"))["configurable"].get("checkpoint_id"))
        return saved

    async def aput_writes(self, *args: Any, **kwargs: Any) -> None:
        """Store a task's pending writes, if the lease is still held."""
        await self._fence()
        await super().aput_writes(*args, **kwargs)
