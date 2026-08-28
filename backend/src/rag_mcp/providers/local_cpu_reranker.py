"""Local CPU Reranker using sentence-transformers CrossEncoder (002).

Loads BAAI/bge-reranker-v2-m3 via sentence-transformers CrossEncoder on local
CPU. Reranks a limited set of fused candidates (blueprint §18.5, FR-005).

Deterministic tie-breaker: (rerank_score_desc, fused_score_desc, chunk_id_asc)
— no random perturbation (FR-017, Constitution principle VI).

Blueprint §18.2, FR-004/FR-005/FR-017.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rag_mcp.config import get_settings
from rag_mcp.providers.base import RerankerProvider

logger = logging.getLogger(__name__)


class LocalCPUReranker(RerankerProvider):
    """CrossEncoder-based reranker running on local CPU.

    Loads BAAI/bge-reranker-v2-m3 (or the model configured in settings) lazily.
    The first rerank call triggers model download/loading (~560MB for m3).
    """

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.hybrid_retrieval.reranker_model
        self._model: Any = None

    def _load_model(self) -> Any:
        """Load the CrossEncoder model if not already loaded."""
        if self._model is None:
            logger.info("Loading reranker model %s (first use)...", self._model_name)
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name)
            logger.info("Reranker model %s loaded", self._model_name)
        return self._model

    def warmup(self) -> None:
        """Eagerly load the model so the first request does not block."""
        self._load_model()

    async def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Rerank candidates by CrossEncoder relevance to the query.

        Args:
            query: The search query string.
            candidates: List of candidate dicts with at least 'content_text'
                and 'chunk_id' keys. May also carry 'fused_score'.
            top_k: Number of top results to return.

        Returns:
            Reranked list of candidate dicts with added 'rerank_score' key,
            sorted by (rerank_score_desc, fused_score_desc, chunk_id_asc).
        """
        if not candidates:
            return []

        model = self._load_model()

        # Build query-passage pairs
        passages = [c.get("content_text", "") for c in candidates]
        pairs = [(query, p) for p in passages]

        # Run CrossEncoder prediction in a thread (CPU-bound)
        scores = await asyncio.to_thread(model.predict, pairs)

        # Enrich candidates with rerank_score
        enriched: list[dict[str, Any]] = []
        for i, cand in enumerate(candidates):
            enriched_cand = dict(cand)  # shallow copy to preserve original
            enriched_cand["rerank_score"] = float(scores[i])
            enriched.append(enriched_cand)

        # Sort with deterministic tie-breaker:
        # (rerank_score_desc, fused_score_desc, chunk_id_asc)
        def _sort_key(c: dict[str, Any]) -> tuple:
            rerank_score = c.get("rerank_score", 0.0)
            fused_score = c.get("fused_score", 0.0)
            chunk_id = str(c.get("chunk_id", ""))
            return (
                -rerank_score,   # descending rerank score
                -fused_score,    # descending fused score
                chunk_id,         # ascending chunk_id
            )

        enriched.sort(key=_sort_key)

        # Trim to top_k
        return enriched[:top_k]
