"""Host-side tests trust the compose stack's development CA (deploy/certs, ADR 0015)."""

import os
from pathlib import Path

CA_FILE = str(Path(__file__).resolve().parents[1] / "deploy" / "certs" / "ca.crt")
os.environ.setdefault("INFRA_CA_FILE", CA_FILE)  # chat_common.kafka and Settings
os.environ.setdefault("GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", CA_FILE)  # Qdrant over gRPC
