"""Worker process entry point: `python -m worker.main`."""

import asyncio
import logging
import signal

import redis.asyncio as aioredis
from chat_common.logjson import configure_logging
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from chat_common.tracing import setup_tracing
from confluent_kafka.aio import AIOConsumer, AIOProducer
from qdrant_client import AsyncQdrantClient

from worker.checkpoint import FencedRedisSaver
from worker.consumer import Worker
from worker.embedding import get_embedder
from worker.graph import build_graph, make_processor
from worker.llm import Engine, MockEngine, OpenAICompatEngine
from worker.observability import LangfuseObserver, LLMObserver, NullObserver
from worker.rag_auth import ClientCredentialsTokens, parse_tenant_clients
from worker.rag_client import RagClient, parse_tenant_tokens
from worker.router import Router


def make_observer(settings: Settings) -> LLMObserver:
    """LangFuse when both keys are set; otherwise a no-op (logged once)."""
    pk, sk = settings.langfuse_public_key, settings.langfuse_secret_key
    if pk is None or sk is None:
        logging.getLogger("worker").warning("LangFuse keys not set; LLM traces disabled")
        return NullObserver()
    return LangfuseObserver(
        pk.get_secret_value(), sk.get_secret_value(), settings.langfuse_base_url
    )


def make_engine(settings: Settings) -> Engine:
    """The mock only when MOCK_LLM=true; otherwise the OpenAI-compatible URL in compose."""
    if settings.mock_llm:
        return MockEngine()
    return OpenAICompatEngine(
        settings.llm_base_url,
        settings.llm_model,
        settings.llm_max_tokens,
        settings.inference_timeout_s,
    )


async def run(settings: Settings) -> None:
    """Wire everything and consume until SIGTERM/SIGINT."""
    tenant_tokens = parse_tenant_tokens(settings.rag_tenant_tokens.get_secret_value())  # fail first
    clients = parse_tenant_clients(settings.rag_tenant_clients.get_secret_value())
    token_provider = ClientCredentialsTokens(settings.rag_token_url, clients) if clients else None
    setup_tracing("worker", settings.otel_exporter_otlp_endpoint)
    scrubber = get_scrubber(settings.spacy_model)
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        grpc_port=settings.qdrant_grpc_port,
        prefer_grpc=settings.qdrant_prefer_grpc,
    )
    router = Router(
        get_embedder(settings.embed_model),
        qdrant,
        intents_collection=settings.qdrant_collection,
        injection_collection=settings.qdrant_injection_collection,
        benign_collection=settings.qdrant_benign_collection,
        route_threshold=settings.route_threshold,
        injection_margin=settings.injection_margin,
    )
    consumer = AIOConsumer(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": settings.kafka_consumer_group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    dlq = AIOProducer({"bootstrap.servers": settings.kafka_bootstrap_servers})
    rag = RagClient(
        settings.rag_base_url,
        settings.rag_timeout_s,
        tenant_tokens,
        token_provider=token_provider,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows event loops do not support signal handlers
            pass
    ttl = {"default_ttl": settings.checkpoint_ttl_min, "refresh_on_read": True}
    async with FencedRedisSaver.from_conn_string(settings.redis_url, ttl=ttl) as saver:
        await saver.asetup()
        graph = build_graph(
            router,
            make_engine(settings),
            scrubber.ascrub,
            settings.llm_max_tokens,
            redis=redis,
            observer=make_observer(settings),
            model="mock" if settings.mock_llm else settings.llm_model,
            rag=rag,
            rag_max_tokens=settings.rag_max_tokens,
            rag_context_chars=settings.rag_context_chars,
        )
        process = make_processor(
            graph.compile(checkpointer=saver), redis=redis, approval_ttl_s=settings.approval_ttl_s
        )
        worker = Worker(settings, consumer=consumer, dlq_producer=dlq, redis=redis, process=process)
        try:
            await worker.run(settings.kafka_topic, stop)
        finally:
            await consumer.close()
            await dlq.close()
            await rag.aclose()
            if token_provider is not None:
                await token_provider.aclose()
            await qdrant.close()
            await redis.aclose()


def main() -> None:
    """Configure logging and run; settings validation failures exit non-zero before any I/O."""
    settings = Settings()
    configure_logging(settings.log_level)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
