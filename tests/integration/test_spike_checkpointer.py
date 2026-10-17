"""Spike (a): pinned langgraph-checkpoint-redis writes and reads a checkpoint on redis:8.10.2."""

import operator
import uuid
from typing import Annotated, TypedDict

import pytest
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, START, StateGraph

pytestmark = pytest.mark.integration


class _State(TypedDict):
    """Minimal state: an append-only list of turns."""

    turns: Annotated[list[str], operator.add]


def _echo(state: _State) -> dict[str, list[str]]:
    """Append one marker per invocation."""
    return {"turns": [f"seen-{len(state['turns'])}"]}


async def test_checkpoint_persists_across_saver_instances(redis_url: str) -> None:
    """State written by one saver is read back by a fresh saver on the same thread_id."""
    builder = StateGraph(_State)
    builder.add_node("echo", _echo)
    builder.add_edge(START, "echo")
    builder.add_edge("echo", END)
    config = {"configurable": {"thread_id": f"spike-{uuid.uuid4().hex}"}}

    async with AsyncRedisSaver.from_conn_string(redis_url, ttl={"default_ttl": 5}) as saver:
        await saver.asetup()
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({"turns": ["hello"]}, config)
        await graph.ainvoke({"turns": ["again"]}, config)

    async with AsyncRedisSaver.from_conn_string(redis_url) as fresh:
        await fresh.asetup()
        stored = await fresh.aget_tuple(config)

    assert stored is not None
    assert stored.checkpoint["channel_values"]["turns"] == ["hello", "seen-1", "again", "seen-3"]
