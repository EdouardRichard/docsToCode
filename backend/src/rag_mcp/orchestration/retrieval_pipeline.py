"""Agentic retrieval pipeline for state machine steps 4/5 (T058).

Wires the state machine's parallel-retrieval and fusion/rerank steps to the
REAL retrieval stack, reusing 002/004 components (no reimplementation):
  - QdrantStore dense+sparse hybrid recall (002)
  - RRF fusion + Rerank (002)
  - 004 graph expansion with its guardrails, planner-directed (FR-033)
  - per-source evidence cap default 3 / limit 5 (FR-006)

Candidates carry retriever / score / source / version metadata so they can
enter the append-only evidence ledger (FR-008/FR-009) and supplementary-round
candidates re-enter fusion/Rerank/analysis unchanged (FR-014).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from sqlalchemy import select as sa_select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.models.chunk import Chunk
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.models.project import Project
from rag_mcp.providers.base import EmbeddingProvider, RerankerProvider
from rag_mcp.services.retrieval_service import RetrievalService

logger = logging.getLogger(__name__)

# 004 deterministic bidirectional default (FR-033)
BIDIRECTIONAL_DEFAULT = ["calls", "called_by", "fk_references", "fk_referenced_by"]
VALID_DIRECTIONS = {"calls", "called_by", "fk_references", "fk_referenced_by"}

# RRF fused scores are small (~1/(60+rank)); scale into [0, 1] when no
# dense/sparse/rerank score is available (graph-only candidates).
_RRF_SCALE = 15.0


def clamp01(value: float | None) -> float:
    """Clamp a score into the ledger-valid [0, 1] band."""
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def final_candidate_score(
    dense_score: float | None,
    sparse_score: float | None,
    rerank_score: float | None,
    fused_score: float,
    structure_weight: float | None,
) -> float:
    """Deterministic ledger score selection for a fused candidate.

    Preference (T069, SC-002/SC-015 baseline parity): rerank_score >
    normalized fused score > best(dense/sparse) > structure_weight.
    Always within [0, 1] after clamp01 by the caller.

    The fused RRF score drives the ordering whenever it is available
    (hybrid recall) because the deterministic baseline orders evidence
    by exactly that fused score: with the same criterion a
    single-sub-problem (original-query) agentic run reproduces the
    baseline ranking byte-for-byte. Raw dense/sparse magnitudes would
    systematically promote broad class-level chunks over the precise
    multi-path agreed chunk the baseline ranks first. The dense-only
    fallback (fused == 0) falls through to the raw dense score, which
    again matches the dense-only baseline ordering.
    """
    if rerank_score is not None:
        return float(rerank_score)
    if fused_score and fused_score > 0:
        return min(1.0, float(fused_score) * _RRF_SCALE)
    direct = [s for s in (dense_score, sparse_score) if s is not None]
    if direct:
        return float(max(direct))
    if structure_weight is not None:
        return float(structure_weight)
    return 0.0


def map_graph_params(
    signals: list[str],
    relation_directions: list[str] | None,
) -> tuple[bool, list[str] | None]:
    """Map planner signals/directions to 004 expansion params (FR-033).

    Returns (use_graph, relation_types). Invalid or missing directions fall
    back to the 004 deterministic bidirectional default.
    """
    if "graph" not in (signals or []):
        return False, None
    directions = list(relation_directions or [])
    if not directions or not all(d in VALID_DIRECTIONS for d in directions):
        return True, list(BIDIRECTIONAL_DEFAULT)
    return True, directions


def merge_round_candidates(
    per_group_candidates: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Merge candidate lists (per sub-problem or per round) deterministically.

    Deduplicates by evidence/chunk id, unions sub_problem_ids (monotonic
    order, FR-009), keeps the best score, and sorts by score descending.
    The tie-break mirrors the deterministic baseline RRF ordering
    (fused_score desc, dense_rank asc, sparse_rank asc, chunk_id asc) —
    dense_rank asc == dense_score desc, sparse_rank asc == sparse_score
    desc — so a single sub-problem (original-query) run reproduces the
    baseline ranking exactly (T069, SC-002/SC-015). chunk_id-asc alone
    would flip the precise dense-rank-1 chunk behind the broad class
    chunk on fused-score ties.
    """
    merged: dict[str, dict[str, Any]] = {}
    for group in per_group_candidates:
        for cand in group:
            eid = str(cand.get("evidence_id") or cand.get("chunk_id") or "")
            if not eid:
                continue
            sub_id = cand.get("sub_problem_id")
            sub_ids = list(cand.get("sub_problem_ids") or ([sub_id] if sub_id else []))
            if eid not in merged:
                entry = dict(cand)
                entry["evidence_id"] = eid
                entry["sub_problem_ids"] = sorted(set(sub_ids))
                merged[eid] = entry
            else:
                entry = merged[eid]
                existing_ids = set(entry.get("sub_problem_ids") or [])
                entry["sub_problem_ids"] = sorted(existing_ids | set(sub_ids))
                if float(cand.get("score", 0.0)) > float(entry.get("score", 0.0)):
                    kept_subs = entry["sub_problem_ids"]
                    entry.update(cand)
                    entry["evidence_id"] = eid
                    entry["sub_problem_ids"] = kept_subs
                retrievers = list(entry.get("retrievers") or [])
                for r in cand.get("retrievers") or []:
                    if r not in retrievers:
                        retrievers.append(r)
                entry["retrievers"] = retrievers
    def _order_key(c: dict[str, Any]):
        # Tie-break mirrors the baseline RRF ordering: fused_score desc,
        # then dense_rank asc (== dense_score desc within one list),
        # sparse_rank asc, chunk_id asc. Raw scores are used rather than
        # list ranks because the merge spans MULTIPLE sub-problem/round
        # lists, where per-list ranks are not comparable but raw cosine
        # scores still are (T069, SC-002/SC-015 recall parity).
        def _neg(value: Any) -> float:
            return -float(value) if value is not None else float("inf")

        return (
            -float(c.get("score", 0.0)),
            _neg(c.get("dense_score")),
            _neg(c.get("sparse_score")),
            c.get("evidence_id", ""),
        )

    result = sorted(merged.values(), key=_order_key)
    return result


def apply_per_source_guard(
    candidates: list[dict[str, Any]],
    max_per_source: int,
) -> list[dict[str, Any]]:
    """Enforce the per-source evidence cap (FR-006, default 3 / limit 5).

    Candidates are assumed sorted by score descending; order is preserved.
    """
    counts: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for c in candidates:
        sid = str(c.get("source_id", ""))
        if counts.get(sid, 0) < max_per_source:
            kept.append(c)
            counts[sid] = counts.get(sid, 0) + 1
    return kept


class AgenticRetrievalPipeline:
    """Real retrieval behind state machine steps 4/5 (T058, FR-005/FR-006).

    Each sub-problem query is recalled through the 002 hybrid stack
    (QdrantStore dense+sparse + RRF + Rerank); graph signals expand through
    the 004 graph with its guardrails. Round candidates are merged across
    sub-problems with sub_problem_id traceability and capped per source.
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        qdrant_store: QdrantStore,
        embedding_provider: EmbeddingProvider,
        reranker: RerankerProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._qdrant_store = qdrant_store
        self._embedding_provider = embedding_provider
        self._reranker = reranker
        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def _recall_one(
        self,
        sp: dict[str, Any],
        scope_ids: list[int],
        over_fetch: int,
    ) -> dict[str, Any] | None:
        """Recall one sub-problem path on its own session (parallel unit).

        FR-005 / blueprint sec 12 step 4: retrieval paths run PARALLEL.
        Each path gets a fresh session so recalls can proceed
        concurrently (latency = slowest path, not the sum of paths).
        """
        query = sp.get("query", "")
        if not query:
            return None
        sub_id = int(sp.get("sub_problem_id", 1))
        use_graph, relation_types = map_graph_params(
            sp.get("signals") or ["dense"],
            sp.get("relation_directions"),
        )
        async with self._session_factory() as session:
            service = RetrievalService(
                session=session,
                qdrant_store=self._qdrant_store,
                embedding_provider=self._embedding_provider,
                reranker=self._reranker,
            )
            res = await service.recall_candidates(
                query=query,
                scope_ids=scope_ids,
                limit=over_fetch,
                use_graph=use_graph,
                graph_relation_types=relation_types,
                graph_hop=sp.get("graph_hop"),
            )
        cands = res["candidates"]
        for c in cands:
            c["sub_problem_id"] = sub_id
            c["retrieval_query"] = query
        return {
            "candidates": cands,
            "subpath_timings": res.get("subpath_timings") or {},
            "failed_paths": res.get("failed_paths") or [],
            "graph_used": bool(res.get("graph_used")),
        }

    async def retrieve_round(
        self,
        sub_problems: list[dict[str, Any]],
        scope_ids: list[int],
        round_index: int,
    ) -> dict[str, Any]:
        """Run step-4 parallel recall + step-5 fusion/rerank for one round.

        The per-sub-problem recalls run CONCURRENTLY via asyncio.gather
        (FR-005 parallel retrieval paths). A single failing path is
        isolated (recorded in failed_paths, blueprint sec 19
        degradation) instead of aborting the whole round.

        Returns:
            Dict with 'candidates' (enriched, merged, per-source-capped),
            'subpath_timings', 'failed_paths', 'graph_used'.
        """
        agentic_cfg = self._settings.agentic
        over_fetch = max(agentic_cfg.guardrails.top_k_max, 20)

        recall_results = await asyncio.gather(
            *[
                self._recall_one(sp, scope_ids, over_fetch)
                for sp in sub_problems
            ],
            return_exceptions=True,
        )

        per_sub: list[list[dict[str, Any]]] = []
        timings: dict[str, float] = {}
        failed_paths: list[str] = []
        graph_used_any = False

        for sp, result in zip(sub_problems, recall_results):
            if result is None:
                continue
            if isinstance(result, BaseException):
                # Isolate the failing path (never abort the round)
                failed_paths.append(
                    f"recall_failed:{sp.get('query', '')[:60]}",
                )
                logger.warning(
                    "Sub-problem recall failed (isolated): %s", result,
                )
                continue
            per_sub.append(result["candidates"])
            for key, value in result["subpath_timings"].items():
                timings[key] = round(timings.get(key, 0.0) + float(value), 4)
            failed_paths.extend(result["failed_paths"])
            graph_used_any = graph_used_any or result["graph_used"]

        merged = merge_round_candidates(per_sub)

        async with self._session_factory() as session:
            enriched = await self._enrich_candidates(session, merged, scope_ids)

        guarded = apply_per_source_guard(
            enriched, agentic_cfg.max_evidence_per_source,
        )

        logger.info(
            "Agentic round %d recall: sub_problems=%d merged=%d guarded=%d "
            "graph_used=%s failed=%s",
            round_index, len(sub_problems), len(merged), len(guarded),
            graph_used_any, failed_paths,
        )
        return {
            "candidates": guarded,
            "subpath_timings": timings,
            "failed_paths": failed_paths,
            "graph_used": graph_used_any,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _enrich_candidates(
        self,
        session: AsyncSession,
        candidates: list[dict[str, Any]],
        scope_ids: list[int],
    ) -> list[dict[str, Any]]:
        """Attach source/version/position/content metadata from PostgreSQL.

        Ledger input requires source_version, source_position, project_id and
        index_version (isolation triple) per candidate (FR-008/FR-009).
        """
        if not candidates:
            return []

        chunk_ids: list[int] = []
        version_ids: set[int] = set()
        for c in candidates:
            payload = c.get("payload") or {}
            try:
                chunk_ids.append(int(payload.get("chunk_id", c.get("chunk_id", 0))))
            except (TypeError, ValueError):
                pass
            try:
                version_ids.add(int(payload.get("version_id", 0)))
            except (TypeError, ValueError):
                pass

        chunk_map: dict[int, Chunk] = {}
        if chunk_ids:
            result = await session.execute(
                sa_select(Chunk).where(Chunk.chunk_id.in_(chunk_ids))
            )
            for chunk in result.scalars().all():
                chunk_map[chunk.chunk_id] = chunk

        # 001 parent backfill reuse (T065): fetch parent chunks so the
        # orchestrator can supplement parent scope on demand.
        parent_ids = sorted({
            c.parent_chunk_id for c in chunk_map.values()
            if c.parent_chunk_id is not None
        })
        parent_map: dict[int, Chunk] = {}
        if parent_ids:
            presult = await session.execute(
                sa_select(Chunk).where(Chunk.chunk_id.in_(parent_ids))
            )
            for chunk in presult.scalars().all():
                parent_map[chunk.chunk_id] = chunk
                if chunk.version_id not in version_ids:
                    version_ids.add(chunk.version_id)

        version_number_map: dict[int, int] = {}
        version_scope_map: dict[int, int] = {}
        if version_ids:
            vresult = await session.execute(
                sa_select(
                    KnowledgeVersion.version_id,
                    KnowledgeVersion.version_number,
                    KnowledgeVersion.knowledge_scope_id,
                ).where(KnowledgeVersion.version_id.in_(version_ids))
            )
            for vid, vnum, sid in vresult.all():
                version_number_map[vid] = vnum
                version_scope_map[vid] = sid

        # scope -> project_id (isolation triple)
        project_map: dict[int, int] = {}
        if scope_ids:
            presult = await session.execute(
                sa_select(Project.knowledge_scope_id, Project.project_id).where(
                    Project.knowledge_scope_id.in_(scope_ids)
                )
            )
            for sid, pid in presult.all():
                project_map[sid] = pid

        enriched: list[dict[str, Any]] = []
        for c in candidates:
            payload = c.get("payload") or {}
            try:
                chunk_id_int = int(payload.get("chunk_id", c.get("chunk_id", 0)))
            except (TypeError, ValueError):
                chunk_id_int = 0
            try:
                version_id = int(payload.get("version_id", 0))
            except (TypeError, ValueError):
                version_id = 0
            chunk = chunk_map.get(chunk_id_int)
            try:
                scope_id = int(payload.get("knowledge_scope_id", 0))
            except (TypeError, ValueError):
                scope_id = version_scope_map.get(version_id, 0)
            version_number = version_number_map.get(version_id, 1)
            entry = {
                **c,
                "evidence_id": c.get("chunk_id", str(chunk_id_int)),
                "source_id": str(payload.get("source_id", "")),
                "source_version": version_number,
                "source_position": (
                    chunk.position_path if chunk is not None
                    else payload.get("position_path", "")
                ),
                "knowledge_scope_id": scope_id,
                "knowledge_scope_type": "project",
                "project_id": project_map.get(scope_id, 0),
                "index_version": version_number,
                "content_excerpt": (chunk.content_text[:500] if chunk is not None else ""),
            }
            # Parent scope metadata for on-demand supplementation (T065)
            if chunk is not None and chunk.parent_chunk_id is not None:
                parent = parent_map.get(chunk.parent_chunk_id)
                if parent is not None:
                    parent_version = version_number_map.get(parent.version_id, 1)
                    entry["parent"] = {
                        "chunk_id": str(parent.chunk_id),
                        "content_excerpt": (parent.content_text or "")[:500],
                        "position_path": parent.position_path or "",
                        "source_id": str(parent.source_id),
                        "source_version": parent_version,
                        "knowledge_scope_id": parent.knowledge_scope_id,
                        "knowledge_scope_type": "project",
                        "project_id": project_map.get(parent.knowledge_scope_id, 0),
                        "index_version": parent_version,
                    }
            enriched.append(entry)
        return enriched
