"""Runtime configuration from the environment; defaults mirror `.env.example`."""

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All config keys. Construction fails loudly on an unsafe combination (SPEC.md §5)."""

    model_config = SettingsConfigDict(extra="ignore")

    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_partitions: int = 4
    kafka_topic: str = "chat-requests"
    kafka_dlq_topic: str = "chat-requests.dlq"
    kafka_consumer_group: str = "chat-workers"

    redis_url: str = "redis://redis:6379/0"

    qdrant_url: str = "http://qdrant:6333"
    qdrant_grpc_port: int = 6334
    qdrant_prefer_grpc: bool = True
    qdrant_collection: str = "intents"
    qdrant_injection_collection: str = "injection_patterns"
    qdrant_benign_collection: str = "benign_patterns"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    route_threshold: float = 0.5821  # ADR 0004
    injection_margin: float = -0.0149  # ADR 0004 addendum 2026-10-18 (LOO-calibrated)

    spacy_model: str = "en_core_web_lg"

    llm_base_url: str = "http://ollama:11434/v1"
    llm_model: str = "llama3.2:1b"
    llm_max_tokens: int = 256
    mock_llm: bool = True
    inference_timeout_s: float = 60
    max_retries: int = 2
    retry_backoff_max_s: float = 4

    dedupe_lease_s: int = 200
    dedupe_ttl_s: int = 777600  # 9 days: outlives Kafka retention + one segment (SPEC §5)
    kafka_retention_ms: int = 604800000  # 7 days; applied to the topic by kafka-init
    kafka_segment_ms: int = 86400000  # 1 day; deletion happens per closed segment

    checkpoint_ttl_min: int = 1440
    stream_maxlen: int = 2000
    stream_ttl_s: int = 3600
    stream_block_ms: int = 5000

    max_message_chars: int = 4000
    rate_limit_per_min: int = 20
    cors_origins: str = "http://localhost:5173"
    auth_users: SecretStr = SecretStr("")

    approval_ttl_s: int = 3600

    otel_exporter_otlp_endpoint: str = "http://jaeger:4317"
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str = "https://us.cloud.langfuse.com"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _lease_outlives_retries(self) -> "Settings":
        """A lease shorter than the worst-case processing time would allow double processing."""
        worst = (
            self.inference_timeout_s * (self.max_retries + 1)
            + self.retry_backoff_max_s * self.max_retries
        )
        if not self.dedupe_lease_s > worst:
            raise ValueError(
                f"DEDUPE_LEASE_S={self.dedupe_lease_s} must exceed "
                f"INFERENCE_TIMEOUT_S*(MAX_RETRIES+1)+RETRY_BACKOFF_MAX_S*MAX_RETRIES={worst:g}"
            )
        return self

    @model_validator(mode="after")
    def _dedupe_outlives_kafka_retention(self) -> "Settings":
        """Kafka can redeliver a record (after lost offsets) until retention plus one segment.

        A dedupe `done` marker that expired earlier would let that redelivery run as a new turn.
        """
        horizon_ms = self.kafka_retention_ms + self.kafka_segment_ms
        if not self.dedupe_ttl_s * 1000 > horizon_ms:
            raise ValueError(
                f"DEDUPE_TTL_S={self.dedupe_ttl_s} must exceed "
                f"(KAFKA_RETENTION_MS+KAFKA_SEGMENT_MS)/1000={horizon_ms / 1000:g}"
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS allowlist; a wildcard is rejected rather than silently allowed."""
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if "*" in origins:
            raise ValueError("CORS_ORIGINS must not contain '*'")
        return origins
