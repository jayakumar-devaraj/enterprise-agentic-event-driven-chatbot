"""RAG client (D3, D5): status mapping, one deadline, headers, and the contract (SPEC.md §13)."""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from worker.consumer import TransientDependencyError
from worker.rag_client import RagClient, RagRejected, Retrieval, Section, parse_tenant_tokens

CONTRACT = json.loads(
    (Path(__file__).parents[2] / "docs" / "contracts" / "rag-openapi-v1.json").read_text("utf-8")
)
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
SECTION = {
    "page_id": 7,
    "doc_key": "handbook.txt",
    "page_key": "page:1",
    "source_name": "handbook.txt",
    "markdown": "Servers cost 4200.",
    "score": 0.03,
    "chunk_ids": [11],
}
OK = {"sections": [SECTION], "insufficient": False, "queries_run": ["q"], "attempts": 1}


class Recorder:
    """TEST DOUBLE for the RAG service at the HTTP boundary: answers by `handler`, records calls."""

    def __init__(self, handler) -> None:
        self.handler, self.requests = handler, []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self.handler(request)


def _client(handler, *, timeout_s: float = 5, tokens=None) -> tuple[RagClient, Recorder]:
    rec = Recorder(handler)
    client = RagClient(
        "http://rag.test",
        timeout_s,
        {"T1": "tok-1"} if tokens is None else tokens,
        transport=httpx.MockTransport(rec),
    )
    return client, rec


def _reply(status: int, body=None):
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body if body is not None else {"detail": "x"})

    return handler


async def test_success_parses_the_contract_and_sends_only_the_question() -> None:
    client, rec = _client(_reply(200, OK))
    got = await client.retrieve("T1", "what do servers cost?", traceparent=TRACEPARENT)
    assert got.sections[0].markdown == "Servers cost 4200." and not got.insufficient
    (req,) = rec.requests
    assert json.loads(req.content) == {"question": "what do servers cost?"}  # no tenant in the body
    assert req.url.path == "/v1/retrieve"


async def test_headers_carry_the_token_the_trace_and_the_deadline() -> None:
    """D5: traceparent plus the service's request-id header, set from the chatbot's trace id."""
    client, rec = _client(_reply(200, OK), timeout_s=20)
    await client.retrieve("T1", "q", traceparent=TRACEPARENT)
    h = rec.requests[0].headers
    assert h["authorization"] == "Bearer tok-1"
    assert h["x-request-id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert h["traceparent"] == TRACEPARENT
    assert h["x-timeout-ms"] == "19500"  # a margin shorter than ours, so its 504 can reach us


async def test_without_a_trace_the_request_id_is_fresh_and_safe() -> None:
    client, rec = _client(_reply(200, OK))
    await client.retrieve("T1", "q")
    h = rec.requests[0].headers
    assert len(h["x-request-id"]) == 32 and "traceparent" not in h


@pytest.mark.parametrize("status", [503, 504])
async def test_503_and_504_are_retryable_and_not_retried_here(status: int) -> None:
    client, rec = _client(_reply(status))
    with pytest.raises(TransientDependencyError):
        await client.retrieve("T1", "q")
    assert len(rec.requests) == 1  # retries belong to the consumer


async def test_a_connection_error_is_retryable() -> None:
    async def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client, _ = _client(refuse)
    with pytest.raises(TransientDependencyError):
        await client.retrieve("T1", "q")


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "rag_unauthorized"), (422, "rag_bad_request"), (500, "rag_error"), (404, "rag_error")],
)
async def test_other_statuses_end_the_turn_with_a_fixed_error(status: int, code: str) -> None:
    client, rec = _client(_reply(status))
    with pytest.raises(RagRejected) as err:
        await client.retrieve("T1", "q")
    assert err.value.code == code and len(rec.requests) == 1
    assert "detail" not in err.value.detail  # a fixed sentence, never the service's text


async def test_a_body_off_contract_is_a_final_error_not_a_crash() -> None:
    client, _ = _client(_reply(200, {"sections": "nope"}))
    with pytest.raises(RagRejected) as err:
        await client.retrieve("T1", "q")
    assert err.value.code == "rag_error"


async def test_unknown_tenant_never_reaches_the_network() -> None:
    client, rec = _client(_reply(200, OK))
    with pytest.raises(RagRejected) as err:
        await client.retrieve("T9", "q")
    assert err.value.code == "rag_tenant_unknown" and rec.requests == []


async def test_the_whole_call_has_one_deadline() -> None:
    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=OK)

    client, _ = _client(slow, timeout_s=0.2)
    started = time.perf_counter()
    with pytest.raises(TransientDependencyError):
        await client.retrieve("T1", "q")
    assert time.perf_counter() - started < 2


async def test_fields_the_service_adds_later_are_ignored() -> None:
    client, _ = _client(_reply(200, {**OK, "new_field": 1, "sections": [{**SECTION, "extra": 1}]}))
    assert (await client.retrieve("T1", "q")).attempts == 1


def test_parse_tenant_tokens() -> None:
    assert parse_tenant_tokens(" a1:T1 , b2:T-2 ") == {"T1": "a1", "T-2": "b2"}
    assert parse_tenant_tokens("") == {}


@pytest.mark.parametrize("raw", ["a1", "a1:", ":T1", "a1:T1,b2:T1", "a 1:T1", "a1:bad tenant"])
def test_malformed_tenant_tokens_fail_startup(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_tenant_tokens(raw)


def _schema(name: str) -> dict:
    return CONTRACT["components"]["schemas"][name]


@pytest.mark.parametrize(
    ("model", "name"), [(Retrieval, "RetrieveResponse"), (Section, "ParentSection")]
)
def test_response_models_match_the_published_contract(model, name: str) -> None:
    """Fields and which are required equal docs/contracts/rag-openapi-v1.json, so drift fails."""
    ours, theirs = model.model_json_schema(), _schema(name)
    assert set(ours["properties"]) == set(theirs["properties"])
    assert set(ours["required"]) == set(theirs["required"])
    for field, spec in theirs["properties"].items():
        assert ours["properties"][field].get("type") == spec.get("type") or "$ref" in spec


def test_request_and_status_codes_match_the_published_contract() -> None:
    assert set(_schema("RetrieveRequest")["properties"]) == {"question"}
    post = CONTRACT["paths"]["/v1/retrieve"]["post"]
    assert {"401", "422", "500", "503", "504"} <= set(post["responses"])
