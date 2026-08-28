"""RRF (Reciprocal Rank Fusion) for Dense + Sparse retrieval (002).

Deterministic rank-based fusion: score(d) = Σ_i 1/(k + rank_i(d)).
Tie-breaker: (fused_score_desc, dense_rank_asc, sparse_rank_asc, chunk_id_asc)
— no random perturbation (FR-017, Constitution principle VI).

DBSF (Dense-Sparse Best Score Fusion) is reserved as a configurable
alternative stub (research.md §1.2).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FusedCandidate:
    """A candidate after RRF fusion, with per-retriever scores and ranks.

    Fields match the hybrid-retrieval-trace schema fused_candidates items.
    """

    chunk_id: str
    knowledge_scope_id: str
    source_retrievers: list[str] = field(default_factory=list)
    dense_score: float | None = None
    sparse_score: float | None = None
    dense_rank: int | None = None
    sparse_rank: int | None = None
    fused_score: float = 0.0
    rerank_score: float | None = None
    final_rank: int = 0


def rrf_fuse(
    dense_results: list[dict[str, Any]],
    sparse_results: list[dict[str, Any]],
    k: int = 60,
) -> list[FusedCandidate]:
    """Fuse Dense and Sparse ranked lists using Reciprocal Rank Fusion.

    Args:
        dense_results: Dense retrieval ranked list. Each item must have
            'chunk_id' (str), 'score' (float), and optionally 'payload'
            with 'knowledge_scope_id'.
        sparse_results: Sparse retrieval ranked list, same structure.
        k: RRF constant (default 60). Larger k smooths rank differences.

    Returns:
        List of FusedCandidate sorted by fused_score descending with
        deterministic tie-breaking: (fused_score_desc, dense_rank_asc,
        sparse_rank_asc, chunk_id_asc).
    """
    def _extract_chunk_id(result: dict[str, Any]) -> str:
        """Extract chunk_id from a retrieval result dict.

        Handles QdrantStore format ({id, score, payload}) where chunk_id
        is in payload, and direct format where chunk_id is a top-level key.
        """
        if "chunk_id" in result:
            return str(result["chunk_id"])
        payload = result.get("payload") or {}
        if "chunk_id" in payload:
            return str(payload["chunk_id"])
        return str(result.get("id", ""))

    # Build candidate map: chunk_id -> FusedCandidate
    candidates: dict[str, FusedCandidate] = {}

    # Process Dense ranked list (1-based rank)
    for rank, result in enumerate(dense_results, start=1):
        chunk_id = _extract_chunk_id(result)
        payload = result.get("payload") or {}
        scope_id = str(payload.get("knowledge_scope_id", ""))

        if chunk_id not in candidates:
            candidates[chunk_id] = FusedCandidate(
                chunk_id=chunk_id,
                knowledge_scope_id=scope_id,
            )
        cand = candidates[chunk_id]
        cand.dense_score = float(result.get("score", 0.0))
        cand.dense_rank = rank
        cand.knowledge_scope_id = cand.knowledge_scope_id or scope_id
        if "dense" not in cand.source_retrievers:
            cand.source_retrievers.append("dense")

    # Process Sparse ranked list (1-based rank)
    for rank, result in enumerate(sparse_results, start=1):
        chunk_id = _extract_chunk_id(result)
        payload = result.get("payload") or {}
        scope_id = str(payload.get("knowledge_scope_id", ""))

        if chunk_id not in candidates:
            candidates[chunk_id] = FusedCandidate(
                chunk_id=chunk_id,
                knowledge_scope_id=scope_id,
            )
        cand = candidates[chunk_id]
        cand.sparse_score = float(result.get("score", 0.0))
        cand.sparse_rank = rank
        cand.knowledge_scope_id = cand.knowledge_scope_id or scope_id
        if "sparse" not in cand.source_retrievers:
            cand.source_retrievers.append("sparse")

    # Compute fused score: Σ 1/(k + rank)
    for cand in candidates.values():
        score = 0.0
        if cand.dense_rank is not None:
            score += 1.0 / (k + cand.dense_rank)
        if cand.sparse_rank is not None:
            score += 1.0 / (k + cand.sparse_rank)
        cand.fused_score = score

    # Sort with deterministic tie-breaker:
    # (fused_score_desc, dense_rank_asc, sparse_rank_asc, chunk_id_asc)
    # For None ranks, use infinity so they sort after non-None (lower = better)
    def _sort_key(c: FusedCandidate) -> tuple:
        return (
            -c.fused_score,  # descending fused score
            c.dense_rank if c.dense_rank is not None else float("inf"),  # ascending
            c.sparse_rank if c.sparse_rank is not None else float("inf"),  # ascending
            c.chunk_id,  # ascending chunk_id (string comparison)
        )

    result = sorted(candidates.values(), key=_sort_key)

    # Assign final ranks (1-based)
    for i, cand in enumerate(result, start=1):
        cand.final_rank = i

    return result


def dbsf_fuse(
    dense_results: list[dict[str, Any]],
    sparse_results: list[dict[str, Any]],
    dense_weight: float = 0.5,
    sparse_weight: float = 0.5,
) -> list[FusedCandidate]:
    """DBSF (Dense-Sparse Best Score Fusion) — weighted score fusion stub.

    Reserved as a configurable alternative to RRF (research.md §1.2).
    Requires score normalization which is not implemented in 002 first round.
    """
    raise NotImplementedError(
        "DBSF fusion is reserved as a configurable alternative (research.md §1.2). "
        "Use rrf_fuse for 002 first round."
    )
