"""librdkafka settings every Kafka client starts from (ADR 0015)."""

import os
from typing import Any


def client_config(bootstrap: str) -> dict[str, Any]:
    """`bootstrap.servers` plus SASL/PLAIN as KAFKA_USERNAME (default chatbot) and KAFKA_PASSWORD.

    Extend with `|`; a missing password fails at the first broker call, never silently.
    """
    return {
        "bootstrap.servers": bootstrap,
        "security.protocol": "SASL_PLAINTEXT",
        "sasl.mechanism": "PLAIN",
        "sasl.username": os.environ.get("KAFKA_USERNAME", "chatbot"),
        "sasl.password": os.environ.get("KAFKA_PASSWORD", ""),
    }
