"""LangGraph StateGraph: routing is the first node, then one intent node, then END."""

import logging
import operator
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Annotated, Any, TypedDict

from chat_common.schemas import Envelope
from langgraph.graph import END, START, StateGraph

from worker.consumer import TransientDependencyError
from worker.guards import guarded_tokens
from worker.llm import REFUSAL, Engine, Turn
from worker.router import Router
from worker.streams import StreamWriter

log = logging.getLogger("worker.graph")
current_writer: ContextVar[StreamWriter] = ContextVar("current_writer")


class ChatState(TypedDict, total=False):
    """Checkpointed per thread_id. `turns` holds scrubbed text only."""

    turns: Annotated[list[Turn], operator.add]
    pending: str  # scrubbed user text of the turn being answered (overwritten each turn)
    message_id: str
    user_id: str
    intent: str
    route_score: float
    injection_score: float


async def _single(text: str) -> Any:
    """Async iterator over one fixed reply."""
    yield text


def build_graph(
    router: Router,
    engine: Engine,
    scrub: Callable[[str], Awaitable[str]],
    max_tokens: int,
) -> StateGraph:
    """Assemble the graph; the caller compiles it with a checkpointer."""

    async def route(state: ChatState) -> dict[str, Any]:
        try:
            r = await router.route(state["pending"])
        except Exception as exc:  # Qdrant/embedding faults are retried by the worker
            raise TransientDependencyError(f"route: {exc}") from exc
        intent = "refuse" if r.injection else r.intent
        if r.injection:
            log.warning(
                "prompt injection detected; refusing",
                extra={"message_id": state["message_id"], "injection_score": r.injection_score},
            )
        return {"intent": intent, "route_score": r.score, "injection_score": r.injection_score}

    async def respond(state: ChatState) -> dict[str, Any]:
        """Stream the reply through the output guard; persist the user+assistant pair at once."""
        writer = current_writer.get()
        history = state.get("turns", [])
        if state["intent"] == "refuse":
            deltas = _single(REFUSAL)
        else:
            deltas = engine.stream(state["intent"], state["pending"], history)
        tokens: list[str] = []
        async for token in guarded_tokens(deltas, scrub, max_tokens):
            tokens.append(token)
            await writer.token(token)
        reply = " ".join(tokens)
        return {
            "turns": [
                {"role": "user", "text": state["pending"]},
                {"role": "assistant", "text": reply},
            ]
        }

    builder = StateGraph(ChatState)
    builder.add_node("route", route)
    for name in ("billing", "tech_support", "general", "refuse"):
        builder.add_node(name, respond)
        builder.add_edge(name, END)
    builder.add_edge(START, "route")
    builder.add_conditional_edges("route", lambda s: s["intent"])
    return builder


def make_processor(compiled: Any) -> Callable[[Envelope, StreamWriter], Awaitable[None]]:
    """Adapt a compiled graph to the worker's Processor signature."""

    async def process(env: Envelope, writer: StreamWriter) -> None:
        token = current_writer.set(writer)
        try:
            await compiled.ainvoke(
                {"pending": env.text, "message_id": str(env.message_id), "user_id": env.user_id},
                {"configurable": {"thread_id": env.thread_id}},
            )
        finally:
            current_writer.reset(token)
        await writer.done()

    return process
