"""Local CPU embedding provider using sentence-transformers with BAAI/bge-m3.

Blueprint §18.2: BGE-M3 is the local default embedding model (Dense only for 001).

The model is loaded lazily on first use to avoid blocking startup, and cached
for the process lifetime. Embedding calls are synchronous (sentence-transformers
is CPU-bound) but wrapped in asyncio.to_thread to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rag_mcp.config import get_settings
from rag_mcp.providers.base import EmbeddingProvider

logger = logging.getLogger(__name__)


class LocalCPUEmbeddingProvider(EmbeddingProvider):
    """Sentence-transformers based embedding provider running on local CPU.

    Loads BAAI/bge-m3 (or the model configured in settings) lazily. The first
    embed call triggers model download/loading, which may take time and memory
    (~2GB). Returns 1024-dim dense vectors for bge-m3.
    """

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.embedding_model
        self._model: Any = None
        self._dimension: int | None = None

    def _ensure_model(self) -> Any:
        """Load the sentence-transformers model if not already loaded."""
        if self._model is None:
            logger.info("Loading embedding model %s (first use)...", self._model_name)
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            self._dimension = self._model.get_sentence_embedding_dimension()
            logger.info(
                "Embedding model %s loaded (dim=%d)", self._model_name, self._dimension
            )
        return self._model

    def warmup(self) -> None:
        """Eagerly load the model so the first request does not block/time out.

        bge-m3 is a ~2GB model; loading it lazily on first use makes the first
        ``search_knowledge`` call exceed the client's request timeout. Call this
        at server startup (after the event loop is ready) to front-load the cost.
        """
        self._ensure_model()

    def get_dimension(self) -> int:
        """Return embedding vector dimensionality (1024 for bge-m3)."""
        model = self._ensure_model()
        return self._dimension or model.get_sentence_embedding_dimension()

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into dense vectors.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of float vectors in the same order as input.
        """
        if not texts:
            return []
        model = self._ensure_model()
        # Run synchronous CPU-bound encode in a thread to avoid blocking loop
        embeddings = await asyncio.to_thread(
            model.encode,
            texts,
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return [vec.tolist() for vec in embeddings]

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query text.

        Args:
            text: Query string to embed.

        Returns:
            Float vector.
        """
        vecs = await self.embed_texts([text])
        return vecs[0]
