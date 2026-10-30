"""bge-small-en-v1.5 embeddings via fastembed (ONNX, no PyTorch), shared by worker and seed."""

import asyncio
import os
from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding

DIM = 384
# Model card (BAAI/bge-small-en-v1.5): short queries matched against passages get this instruction;
# passages are embedded without a prefix. fastembed does not add it for this model.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder:
    """Loads the model once; embedding is CPU-bound, so async callers use `aembed_query`."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        """Fail loudly if the model is absent and the hub is offline (HF_HUB_OFFLINE=1).

        One ONNX Runtime thread: by default it starts one per host core (not per container CPU), and
        the idle ones spin, taking CPU from the scrubber and the event loop. Throughput comes from
        replicas, not from splitting one short query embedding across threads.
        """
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir, threads=1)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Embed exemplar texts (no prefix), L2-normalised by the model."""
        return [np.asarray(v, dtype=float).tolist() for v in self._model.embed(texts)]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """Embed messages with the model card's query instruction."""
        return self.embed_passages([QUERY_PREFIX + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        """Embed one incoming message with the model card's query instruction."""
        return self.embed_queries([text])[0]

    async def aembed_query(self, text: str) -> list[float]:
        """Off-loop query embedding."""
        return await asyncio.to_thread(self.embed_query, text)


@lru_cache(maxsize=1)
def get_embedder(model_name: str) -> Embedder:
    """Process-wide embedder; the cache dir comes from FASTEMBED_CACHE_PATH when set."""
    return Embedder(model_name, cache_dir=os.getenv("FASTEMBED_CACHE_PATH"))
