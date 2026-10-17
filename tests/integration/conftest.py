"""Endpoints of the compose stack as seen from the host (deploy/compose.yaml host ports)."""

import os

import pytest

KAFKA = os.getenv("IT_KAFKA_BOOTSTRAP", "127.0.0.1:19092")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:16379/0")
QDRANT_URL = os.getenv("IT_QDRANT_URL", "http://127.0.0.1:16333")
QDRANT_GRPC_PORT = int(os.getenv("IT_QDRANT_GRPC_PORT", "16334"))


@pytest.fixture
def kafka_bootstrap() -> str:
    """Kafka bootstrap servers for host-side tests."""
    return KAFKA


@pytest.fixture
def redis_url() -> str:
    """Redis URL for host-side tests."""
    return REDIS_URL
