"""LangGraph StateGraph: routing is the first node, then one intent node (or the refund gate)."""

import logging
import operator
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, TypedDict

from chat_common.audit import AuditLog
from chat_common.schemas import Envelope
from chat_common.tracing import context_from_traceparent
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from opentelemetry import trace

from worker import approval
from worker.consumer import TransientDependencyError
from worker.guards import guarded_tokens
from worker.llm import REFUSAL, Engine, Turn
from worker.observability import LLMObserver, NullObserver
from worker.router import Router
from worker.streams import StreamWriter

log = logging.getLogger("worker.graph")
current_writer: ContextVar[StreamWriter] = ContextVar("current_writer")
tracer = trace.get_tracer("worker")


class ChatState(TypedDict, total=False):
    """Checkpointed per thread_id. `turns` holds scrubbed text only."""

    turns: Annotated[list[Turn], operator.add]
    pending: str  # scrubbed user text of the turn being answered (overwritten each turn)
    message_id: str
    answered: str  # message_id whose reply is the last assistant turn (replay idempotency)
    user_id: str
    intent: str
    route_score: float
    injection_score: float
    decision: dict[str, Any]
    proposed_at: str  # ISO time the refund approval window opened (SPEC §10)


async def _single(text: str) -> Any:
    """Async iterator over one fixed reply."""
    yield text


def build_graph(
    router: Router,
    engine: Engine,
    scrub: Callable[[str], Awaitable[str]],
    max_tokens: int,
    *,
    redis: Any = None,
    observer: LLMObserver | None = None,
    model: str = "mock",
) -> StateGraph:
    """Assemble the graph; the caller compiles it with a checkpointer."""

    llm_observer: LLMObserver = observer or NullObserver()

    async def route(state: ChatState) -> dict[str, Any]:
        with tracer.start_as_current_span("route") as span:
            try:
                r = await router.route(state["pending"])
            except Exception as exc:  # Qdrant/embedding faults are retried by the worker
                raise TransientDependencyError(f"route: {exc}") from exc
            span.set_attributes(
                {
                    "route.intent": r.intent,
                    "route.score": r.score,
                    "route.injection": r.injection,
                    "route.injection_score": r.injection_score,
                }
            )
        intent = "refuse" if r.injection else r.intent
        if r.injection:
            log.warning(
                "prompt injection detected; refusing",
                extra={"message_id": state["message_id"], "injection_score": r.injection_score},
            )
        return {"intent": intent, "route_score": r.score, "injection_score": r.injection_score}

    def after_route(state: ChatState) -> str:
        if approval.wants_refund(state["intent"], state["pending"]):
            return "propose_refund"
        return state["intent"]

    async def stream_reply(deltas: Any) -> str:
        writer = current_writer.get()
        tokens: list[str] = []
        async for token in guarded_tokens(deltas, scrub, max_tokens):
            tokens.append(token)
            await writer.token(token)
        return " ".join(tokens)

    async def respond(state: ChatState) -> dict[str, Any]:
        """Stream the reply through the output guard; persist the user+assistant pair at once."""
        if state["intent"] == "refuse":
            deltas = _single(REFUSAL)
        else:
            deltas = engine.stream(state["intent"], state["pending"], state.get("turns", []))
        with tracer.start_as_current_span("respond") as span:
            span.set_attributes({"intent": state["intent"], "engine": engine.name})
            reply = await stream_reply(deltas)
            llm_observer.record(
                trace_id=format(span.get_span_context().trace_id, "032x"),
                intent=state["intent"],
                engine="refusal" if state["intent"] == "refuse" else engine.name,
                model=model,
                input={
                    "user_message": state["pending"],
                    "history_turns": len(state.get("turns", [])),
                },
                output=reply,
            )
        return {
            "turns": [
                {"role": "user", "text": state["pending"]},
                {"role": "assistant", "text": reply},
            ],
            "answered": state["message_id"],
        }

    def propose_refund(state: ChatState) -> dict[str, Any]:
        """Checkpoint when the approval window opens, before the pause, so it survives resumes."""
        return {"proposed_at": datetime.now(UTC).isoformat()}

    def await_approval(state: ChatState) -> dict[str, Any]:
        """Pause before any side effect; resumed with {"approved": bool, "user_id": str}."""
        decision = interrupt({"action": "refund_request", "message_id": state["message_id"]})
        return {"decision": decision}

    async def settle_refund(state: ChatState) -> dict[str, Any]:
        """Apply (idempotently) or reject, audit it, and tell the user deterministically."""
        decision = state["decision"]
        thread_id, ref = decision["thread_id"], state["message_id"]
        if decision["approved"]:
            await approval.apply_refund(
                redis,
                thread_id=thread_id,
                ref_message_id=ref,
                user_id=decision["user_id"],
                lease=current_writer.get().lease,
            )
            text = approval.APPROVED_TEXT
        else:
            await AuditLog(redis).record(
                "refund_rejected",
                actor=decision["user_id"],
                thread_id=thread_id,
                ref_message_id=ref,
            )
            text = approval.REJECTED_TEXT
        reply = await stream_reply(_single(text))
        return {
            "turns": [
                {"role": "user", "text": state["pending"]},
                {"role": "assistant", "text": reply},
            ],
            "answered": decision["message_id"],
        }

    builder = StateGraph(ChatState)
    builder.add_node("route", route)
    for name in ("billing", "tech_support", "general", "refuse"):
        builder.add_node(name, respond)
        builder.add_edge(name, END)
    builder.add_node("propose_refund", propose_refund)
    builder.add_node("await_approval", await_approval)
    builder.add_node("settle_refund", settle_refund)
    builder.add_edge(START, "route")
    builder.add_conditional_edges("route", after_route)
    builder.add_edge("propose_refund", "await_approval")
    builder.add_edge("await_approval", "settle_refund")
    builder.add_edge("settle_refund", END)
    return builder


def make_processor(
    compiled: Any, *, redis: Any = None, approval_ttl_s: int = 3600
) -> Callable[[Envelope, StreamWriter], Awaitable[None]]:
    """Adapt a compiled graph to the worker's Processor signature."""

    async def process(env: Envelope, writer: StreamWriter) -> None:
        parent = context_from_traceparent(env.trace_id) if env.trace_id.startswith("00-") else None
        with tracer.start_as_current_span("worker.process", context=parent) as span:
            span.set_attributes(
                {"thread_id": env.thread_id, "message_id": str(env.message_id), "kind": env.kind}
            )
            await _process(env, writer)

    async def _process(env: Envelope, writer: StreamWriter) -> None:
        """Advance the thread by this envelope once, however often it is delivered."""
        config = {"configurable": {"thread_id": env.thread_id}}
        mid = str(env.message_id)
        token = current_writer.set(writer)
        try:
            snapshot = await compiled.aget_state(config)
            values = snapshot.values
            paused = values.get("message_id") if "await_approval" in snapshot.next else None
            if values.get("answered") == mid and not snapshot.next:
                # An earlier attempt checkpointed this reply, then died before `done`: re-send it.
                if env.kind == "approval":
                    await redis.delete(approval.pending_key(env.thread_id, str(env.ref_message_id)))
                for part in values["turns"][-1]["text"].split(" "):
                    await writer.token(part)
            elif env.kind == "approval":
                ref = str(env.ref_message_id)
                if paused != ref:
                    await writer.error("approval_not_pending", "no refund is awaiting approval")
                    return
                # The gateway already checked both; enforce them again where the action is applied.
                if env.user_id != values.get("user_id"):
                    await writer.error("approval_forbidden", "only the requester can decide")
                    return
                proposed = values.get("proposed_at")
                deadline = proposed and datetime.fromisoformat(proposed) + timedelta(
                    seconds=approval_ttl_s
                )
                if not deadline or env.ts > deadline:  # no proposed_at: paused before it existed
                    await redis.delete(approval.pending_key(env.thread_id, ref))
                    await AuditLog(redis).record(
                        "refund_expired", actor=env.user_id, thread_id=env.thread_id, message_id=ref
                    )
                    await writer.error("approval_expired", "the refund approval window has closed")
                    return
                await compiled.ainvoke(
                    Command(
                        resume={
                            "approved": env.approved,
                            "user_id": env.user_id,
                            "thread_id": env.thread_id,
                            "message_id": mid,
                        }
                    ),
                    config,
                )
                await redis.delete(approval.pending_key(env.thread_id, ref))
            else:
                if paused is not None and paused != mid:
                    # A new chat supersedes the paused refund; /approve on it now gets 404.
                    await redis.delete(approval.pending_key(env.thread_id, paused))
                    await AuditLog(redis).record(
                        "refund_abandoned",
                        actor=env.user_id,
                        thread_id=env.thread_id,
                        message_id=paused,
                        superseded_by=mid,
                    )
                if paused == mid:  # paused by an earlier attempt that died before announcing it
                    interrupted = True
                else:
                    result = await compiled.ainvoke(
                        {"pending": env.text, "message_id": mid, "user_id": env.user_id}, config
                    )
                    interrupted = bool(result.get("__interrupt__"))
                if interrupted:
                    await redis.set(
                        approval.pending_key(env.thread_id, mid), env.user_id, ex=approval_ttl_s
                    )
                    await AuditLog(redis).record(
                        "refund_proposed",
                        actor=env.user_id,
                        thread_id=env.thread_id,
                        message_id=mid,
                    )
                    await writer.approval_required("A refund request needs your approval.")
                    return
        finally:
            current_writer.reset(token)
        await writer.done()

    return process
