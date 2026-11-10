"""Endpoints of the compose stack as seen from the host (deploy/compose.yaml host ports)."""

import asyncio
import os
import sys

import pytest

if sys.platform == "win32":  # psycopg async (the audit store) cannot run on the Proactor loop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

KAFKA = os.getenv("IT_KAFKA_BOOTSTRAP", "127.0.0.1:19092")
REDIS_URL = os.getenv(
    "REDIS_URL",
    (
        f"rediss://chatbot:{os.environ.get('REDIS_PASSWORD', '')}@127.0.0.1:16379/0"
        f"?ssl_ca_certs={os.environ['INFRA_CA_FILE']}"
    ),
)
QDRANT_URL = os.getenv("IT_QDRANT_URL", "https://127.0.0.1:16333")
QDRANT_GRPC_PORT = int(os.getenv("IT_QDRANT_GRPC_PORT", "16334"))
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
# The audit_writer and audit_admin roles of compose `audit-db` (host port 15432).
AUDIT_WRITER_URL = os.getenv("IT_AUDIT_WRITER_URL", "")
AUDIT_ADMIN_URL = os.getenv("IT_AUDIT_ADMIN_URL", "")


@pytest.fixture
def kafka_bootstrap() -> str:
    """Kafka bootstrap servers for host-side tests."""
    return KAFKA


@pytest.fixture
def redis_url() -> str:
    """Redis URL for host-side tests."""
    return REDIS_URL
