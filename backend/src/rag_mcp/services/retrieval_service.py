"""RetrievalService: orchestrates semantic search across the RAG knowledge base.

Resolves project references, embeds queries, searches Qdrant with scope and
version filters, enforces per-source guards, records audit trails, and returns
structured responses conforming to mcp-search-output.schema.json.

Blueprint §12 (retrieval guardrails), §14 (four-state completion status).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.config import get_settings
from rag_mcp.fusion.rrf import rrf_fuse
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder
from rag_mcp.models.chunk import Chunk
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.models.project import Project
from rag_mcp.models.retrieval_run import RetrievalRun
from rag_mcp.providers.base import EmbeddingProvider, RerankerProvider
from rag_mcp.utils.snowflake import generate_id

logger = logging.getLogger(__name__)

# Module-level cache for BM25SparseEncoder instances (T036, FR-015/SC-005).
# Key: sorted tuple of scope_ids. Value: fitted BM25SparseEncoder.
# Hash-based term IDs ensure cached encoder produces compatible IDs with
# stored sparse vectors. Cache is process-scoped; restart rebuilds it.
_sparse_encoder_cache: dict[tuple[int, ...], "BM25SparseEncoder"] = {}


def invalidate_sparse_encoder_cache() -> None:
    """Clear the sparse encoder cache (call after ingestion publishes new data)."""
    global _sparse_encoder_cache
    _sparse_encoder_cache.clear()
    logger.info("Sparse encoder cache invalidated")


class RetrievalService:
    """Orchestrates semantic retrieval with project-scope isolation.

    Responsibilities:
    - Resolve human-friendly project references to internal scope IDs
    - Embed queries and search Qdrant with scope + version filters
    - Enforce max_evidence_per_source guard
    - Determine four-state completion status
    - Record RetrievalRun audit trail
    - Return structured response matching mcp-search-output.schema.json
    """

    def __init__(
        self,
        session: AsyncSession,
        qdrant_store: QdrantStore,
        embedding_provider: EmbeddingProvider,
        reranker: RerankerProvider | None = None,
    ) -> None:
        self._session = session
        self._qdrant_store = qdrant_store
        self._embedding_provider = embedding_provider
        self._reranker = reranker
        self._settings = get_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        project_scopes: list[str],
        top_k: int = 5,
        task_context: dict | None = None,
    ) -> dict[str, Any]:
        """Execute a scoped semantic search and return structured results.

        Args:
            query: Natural language query string.
            project_scopes: List of project references (ID, alias, or repo_path).
            top_k: Maximum number of evidence items to return (1-20).
            task_context: Optional task context for future relevance boosting.

        Returns:
            Dict conforming to mcp-search-output.schema.json.
        """
        request_id = str(uuid.uuid4())
        start_time = time.monotonic()

        # Clamp top_k to configured bounds
        retrieval_cfg = self._settings.retrieval
        top_k = max(1, min(top_k, retrieval_cfg.top_k_max))

        try:
            # 1. Resolve project references to knowledge_scope_ids
            resolved_ids, error_info = await self.resolve_project_refs(project_scopes)

            if error_info is not None:
                # Resolution failed — return error response
                duration_ms = int((time.monotonic() - start_time) * 1000)
                await self._record_retrieval_run(
                    query=query,
                    project_scopes=project_scopes,
                    completion_status="failed",
                    evidence_count=0,
                    duration_ms=duration_ms,
                    retrieval_mode="dense",
                    subpath_timings=None,
                    evidence_ref_ids=[],
                )
                return {
                    "completion_status": "failed",
                    "evidence": [],
                    "error": error_info,
                    "request_id": request_id,
                }

            if not resolved_ids:
                # Should not happen after error check, but defensive
                duration_ms = int((time.monotonic() - start_time) * 1000)
                await self._record_retrieval_run(
                    query=query,
                    project_scopes=project_scopes,
                    completion_status="failed",
                    evidence_count=0,
                    duration_ms=duration_ms,
                    retrieval_mode="dense",
                    subpath_timings=None,
                    evidence_ref_ids=[],
                )
                return {
                    "completion_status": "failed",
                    "evidence": [],
                    "error": {
                        "code": "MISSING_PROJECT_SCOPE",
                        "message": "No valid project scopes could be resolved.",
                    },
                    "request_id": request_id,
                }

            # 2. Embed the query
            query_vector = await self._embedding_provider.embed_query(query)

            # 3. Hybrid or Dense recall based on version capabilities (FR-013)
            #    Only lexical_ready versions participate in the Sparse path.
            subpath_timings: dict | None = None
            retrieval_mode = "dense"

            hybrid_result = await self._try_hybrid_recall(
                query, query_vector, resolved_ids, top_k * 2,
            )
            failed_paths: list[str] = []
            if hybrid_result is not None:
                raw_results, subpath_timings, failed_paths = hybrid_result
                retrieval_mode = "hybrid"
            else:
                # Dense-only path (001 behavior, backward compat)
                index_version = self._derive_index_version()
                collection_name = f"chunks_dense_{index_version}"
                raw_results = self._qdrant_store.search(
                    collection=collection_name,
                    vector=query_vector,
                    scope_ids=resolved_ids,
                    limit=top_k * 2,  # Over-fetch for per-source guard
                )

            # 5. Filter to published versions only
            filtered_results = await self._filter_published_versions(raw_results)

            # 6. Enforce max_evidence_per_source guard
            guarded_results = self._apply_per_source_guard(
                filtered_results,
                max_per_source=retrieval_cfg.max_evidence_per_source,
            )

            # 7. Trim to requested top_k
            final_results = guarded_results[:top_k]

            # 8. Build evidence items
            evidence_items = await self._build_evidence_items(final_results)

            # 9. Determine completion status
            completion_status = self._determine_completion_status(
                evidence_items=evidence_items,
                requested_top_k=top_k,
                total_available=len(filtered_results),
            )

            # 9b. If hybrid sub-paths failed, status is partial (FR-016)
            if failed_paths:
                completion_status = "partial"

            # 10. Build gaps for partial status
            gaps: list[dict[str, str]] = []
            if completion_status == "partial":
                gaps = self._infer_gaps(evidence_items, top_k)
                for fp in failed_paths:
                    gaps.append({
                        "description": f"Retrieval sub-path failed: {fp}",
                        "suggested_action": "Check service health and retry.",
                    })

            # 11. Record RetrievalRun audit trail
            duration_ms = int((time.monotonic() - start_time) * 1000)
            evidence_ref_ids = [e["evidence_id"] for e in evidence_items]
            await self._record_retrieval_run(
                query=query,
                project_scopes=project_scopes,
                completion_status=completion_status,
                evidence_count=len(evidence_items),
                duration_ms=duration_ms,
                retrieval_mode=retrieval_mode,
                subpath_timings=subpath_timings,
                evidence_ref_ids=evidence_ref_ids,
            )

            # 12. Assemble response
            response: dict[str, Any] = {
                "completion_status": completion_status,
                "evidence": evidence_items,
                "request_id": request_id,
            }
            if completion_status == "partial":
                response["gaps"] = gaps

            logger.info(
                "Search completed: status=%s, evidence=%d, duration=%dms, request_id=%s",
                completion_status, len(evidence_items), duration_ms, request_id,
            )
            return response

        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.error(
                "Search failed: %s, request_id=%s", exc, request_id, exc_info=True,
            )
            # Best-effort audit record
            try:
                await self._record_retrieval_run(
                    query=query,
                    project_scopes=project_scopes,
                    completion_status="failed",
                    evidence_count=0,
                    duration_ms=duration_ms,
                    retrieval_mode="dense",
                    subpath_timings=None,
                    evidence_ref_ids=[],
                )
            except Exception:
                logger.error("Failed to record retrieval run for failed search", exc_info=True)

            return {
                "completion_status": "failed",
                "evidence": [],
                "error": {
                    "code": "SYSTEM_ERROR",
                    "message": f"Internal retrieval error: {type(exc).__name__}",
                },
                "request_id": request_id,
            }

    async def resolve_project_refs(
        self, refs: list[str],
    ) -> tuple[list[int], dict | None]:
        """Resolve project references to knowledge_scope_ids.

        Each ref can be:
        - A numeric project_id (string representation of Snowflake ID)
        - An alias string
        - A repo_path string

        Args:
            refs: List of project reference strings.

        Returns:
            Tuple of (resolved_scope_ids, error_info_or_none).
            If any ref is ambiguous, returns error with candidates.
            If no refs resolve, returns error with MISSING_PROJECT_SCOPE.
        """
        resolved_scope_ids: list[int] = []
        all_candidates: list[dict[str, Any]] = []

        for ref in refs:
            ref_stripped = ref.strip()
            if not ref_stripped:
                continue

            projects = await self._find_projects_by_ref(ref_stripped)

            if len(projects) == 0:
                # Ref didn't match anything — skip silently per spec
                # (only fail if NO refs resolve at all)
                logger.warning("Project ref '%s' did not match any project", ref_stripped)
                continue

            if len(projects) > 1:
                # Ambiguous reference — collect candidates for error
                candidates = [
                    {
                        "project_id": str(p.project_id),
                        "name": p.name,
                        "alias": p.alias,
                        "repo_path": p.repo_path,
                    }
                    for p in projects
                ]
                all_candidates.extend(candidates)

                return [], {
                    "code": "AMBIGUOUS_PROJECT_REF",
                    "message": (
                        f"Project reference '{ref_stripped}' matches multiple projects. "
                        f"Please specify using a unique project_id, alias, or repo_path."
                    ),
                    "candidates": candidates,
                }

            # Exactly one match
            project = projects[0]
            scope_id = project.knowledge_scope_id
            if scope_id not in resolved_scope_ids:
                resolved_scope_ids.append(scope_id)

        if not resolved_scope_ids:
            return [], {
                "code": "MISSING_PROJECT_SCOPE",
                "message": (
                    "None of the provided project references could be resolved. "
                    "Please provide valid project IDs, aliases, or repository paths."
                ),
            }

        return resolved_scope_ids, None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _find_projects_by_ref(self, ref: str) -> list[Project]:
        """Find projects matching a reference string.

        Tries matching by:
        1. Numeric project_id
        2. Exact alias match
        3. Exact repo_path match
        4. Partial repo_path match (endswith)

        Returns all matching projects (may be 0, 1, or more).
        """
        conditions = []

        # Try numeric ID match
        try:
            numeric_id = int(ref)
            conditions.append(Project.project_id == numeric_id)
        except ValueError:
            pass

        # Alias match (case-insensitive)
        conditions.append(Project.alias.ilike(ref))

        # Exact repo_path match
        conditions.append(Project.repo_path == ref)

        # Partial repo_path match (for paths like 'org/repo')
        conditions.append(Project.repo_path.ilike(f"%{ref}%"))

        result = await self._session.execute(
            select(Project).where(or_(*conditions))
        )
        return list(result.scalars().all())

    async def _filter_published_versions(
        self, raw_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Filter Qdrant results to only include chunks from published versions.

        Queries the database to verify each result's version_id has status='published'.
        """
        if not raw_results:
            return []

        # Collect unique version_ids from payloads
        version_ids: set[int] = set()
        for r in raw_results:
            vid = r.get("payload", {}).get("version_id")
            if vid is not None:
                try:
                    version_ids.add(int(vid))
                except (ValueError, TypeError):
                    pass

        if not version_ids:
            return []

        # Query published version IDs
        result = await self._session.execute(
            select(KnowledgeVersion.version_id).where(
                KnowledgeVersion.version_id.in_(version_ids),
                KnowledgeVersion.status == "published",
            )
        )
        published_version_ids: set[int] = set(result.scalars().all())

        # Filter results
        return [
            r for r in raw_results
            if int(r.get("payload", {}).get("version_id", 0)) in published_version_ids
        ]

    def _apply_per_source_guard(
        self,
        results: list[dict[str, Any]],
        max_per_source: int,
    ) -> list[dict[str, Any]]:
        """Enforce max_evidence_per_source guard.

        Limits the number of evidence items from any single source_id to
        prevent dominance by one document in results.
        """
        source_counts: dict[str, int] = defaultdict(int)
        guarded: list[dict[str, Any]] = []

        for r in results:
            source_id = str(r.get("payload", {}).get("source_id", ""))
            if source_counts[source_id] < max_per_source:
                guarded.append(r)
                source_counts[source_id] += 1

        return guarded

    async def _build_evidence_items(
        self, results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build EvidenceItem dicts from Qdrant search results.

        Fetches chunk metadata from PostgreSQL to enrich payload data
        with source_version and knowledge_scope_type.
        """
        if not results:
            return []

        # Collect chunk_ids for batch lookup
        chunk_ids: list[int] = []
        for r in results:
            cid = r.get("payload", {}).get("chunk_id")
            if cid is not None:
                try:
                    chunk_ids.append(int(cid))
                except (ValueError, TypeError):
                    pass

        # Batch fetch chunks for enrichment
        chunk_map: dict[int, Chunk] = {}
        if chunk_ids:
            result = await self._session.execute(
                select(Chunk).where(Chunk.chunk_id.in_(chunk_ids))
            )
            for chunk in result.scalars().all():
                chunk_map[chunk.chunk_id] = chunk

        # Also fetch version numbers for each version_id
        version_ids: set[int] = set()
        for r in results:
            vid = r.get("payload", {}).get("version_id")
            if vid is not None:
                try:
                    version_ids.add(int(vid))
                except (ValueError, TypeError):
                    pass

        version_number_map: dict[int, int] = {}
        if version_ids:
            vresult = await self._session.execute(
                select(KnowledgeVersion.version_id, KnowledgeVersion.version_number).where(
                    KnowledgeVersion.version_id.in_(version_ids),
                )
            )
            for vid, vnum in vresult.all():
                version_number_map[vid] = vnum

        # Build evidence items preserving result order
        evidence_items: list[dict[str, Any]] = []
        for r in results:
            payload = r.get("payload", {})
            chunk_id_str = str(payload.get("chunk_id", ""))
            chunk_id_int = int(chunk_id_str) if chunk_id_str else 0
            chunk = chunk_map.get(chunk_id_int)

            version_id = int(payload.get("version_id", 0))
            source_version = version_number_map.get(version_id, 0)

            # Determine scope type from payload; default to 'project' for 001
            # (public knowledge domain is not fully implemented in 001)
            scope_type = "project"

            content_excerpt = ""
            if chunk:
                content_excerpt = chunk.content_text[:500]
            else:
                # Fallback: use position_path from payload
                content_excerpt = payload.get("position_path", "")[:500]

            evidence_item = {
                "evidence_id": chunk_id_str,
                "content_excerpt": content_excerpt,
                "source_version": source_version,
                "source_position": (
                    chunk.position_path if chunk
                    else payload.get("position_path", "")
                ),
                "knowledge_scope_id": str(payload.get("knowledge_scope_id", "")),
                "knowledge_scope_type": scope_type,
                "relevance_score": round(float(r.get("score", 0.0)), 4),
            }
            evidence_items.append(evidence_item)

        return evidence_items

    @staticmethod
    def _determine_completion_status(
        evidence_items: list[dict[str, Any]],
        requested_top_k: int,
        total_available: int,
    ) -> str:
        """Determine the four-state completion status.

        - complete: evidence found and coverage appears sufficient
        - partial: some evidence found but clear gaps exist
        - no_evidence: system executed normally but found nothing
        - failed: handled separately via exception path
        """
        if not evidence_items:
            return "no_evidence"

        # If we got fewer results than requested AND fewer than available,
        # it suggests partial coverage
        if len(evidence_items) < requested_top_k and total_available <= requested_top_k:
            # We returned everything available but it's less than requested
            # This is still "complete" if we have evidence — the knowledge base
            # simply has limited content for this scope
            if len(evidence_items) >= 1:
                return "complete"

        # If we have evidence items, consider it complete for 001
        # Future versions may use reranking scores to determine partial
        return "complete"

    @staticmethod
    def _infer_gaps(
        evidence_items: list[dict[str, Any]],
        requested_top_k: int,
    ) -> list[dict[str, str]]:
        """Infer evidence gaps for partial completion status."""
        gaps: list[dict[str, str]] = []

        if len(evidence_items) < requested_top_k:
            gaps.append({
                "description": (
                    f"Only {len(evidence_items)} evidence item(s) found "
                    f"out of {requested_top_k} requested."
                ),
                "suggested_action": (
                    "Consider broadening the project scope or adding more "
                    "knowledge sources to improve coverage."
                ),
            })

        return gaps

    async def _has_graph_ready_versions(self, scope_ids: list[int]) -> bool:
        """Check if any published version in the given scopes has graph_ready.

        FR-013/FR-014: only graph_ready versions participate in graph expansion.
        """
        result = await self._session.execute(
            select(KnowledgeVersion.version_id).where(
                KnowledgeVersion.knowledge_scope_id.in_(scope_ids),
                KnowledgeVersion.status == "published",
                KnowledgeVersion.graph_ready == True,  # noqa: E712
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _has_graph_edges(self, scope_ids: list[int]) -> bool:
        """Check if graph_edge records exist for the given scopes (FR-013)."""
        from sqlalchemy import text as sa_text
        result = await self._session.execute(
            sa_text(
                "SELECT 1 FROM graph_edge WHERE knowledge_scope_id = ANY(:sids) LIMIT 1"
            ),
            {"sids": list(scope_ids)},
        )
        return result.scalar_one_or_none() is not None

    async def _has_lexical_ready_versions(self, scope_ids: list[int]) -> bool:
        """Check if any published version in the given scopes has lexical_ready.

        FR-013: only lexical_ready versions participate in the Sparse path.
        """
        result = await self._session.execute(
            select(KnowledgeVersion.version_id).where(
                KnowledgeVersion.knowledge_scope_id.in_(scope_ids),
                KnowledgeVersion.status == "published",
                KnowledgeVersion.capabilities["lexical_ready"].astext == "true",
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _try_hybrid_recall(
        self,
        query: str,
        query_vector: list[float],
        scope_ids: list[int],
        limit: int,
    ) -> tuple[list[dict[str, Any]], dict, list[str]] | None:
        """Attempt hybrid Dense+Sparse recall + RRF fusion + Rerank.

        Returns (raw_results, subpath_timings, failed_paths) if hybrid is
        available, or None if no lexical_ready versions or hybrid collection
        missing (caller falls back to dense-only path).

        FR-013: only lexical_ready versions participate in the Sparse path.
        FR-008: both Dense and Sparse enforce scope+version filter.
        FR-016: partial degradation on sparse/rerank failure.
        FR-005: rerank only processes <= rerank_budget candidates.
        """
        # Check for lexical_ready published versions (FR-013)
        if not await self._has_lexical_ready_versions(scope_ids):
            return None

        # Determine hybrid collection name
        index_version = self._derive_index_version()
        collection_name = f"chunks_hybrid_{index_version}"

        # Check hybrid collection exists
        if not self._qdrant_store.collection_exists(collection_name):
            logger.debug(
                "Hybrid collection %s does not exist, falling back to dense",
                collection_name,
            )
            return None

        # Build sparse encoder from published chunk texts in the resolved scopes
        encoder = await self._build_sparse_encoder(scope_ids)
        if encoder is None:
            return None

        # Encode query for sparse retrieval (IDF-weighted, FR-025)
        sparse_query = encoder.encode(query)
        if not sparse_query["indices"]:
            # Query has no in-vocab terms -> sparse path adds nothing
            logger.debug("Query has no in-vocab sparse terms, falling back to dense")
            return None

        # Dense + Sparse parallel recall (FR-002, both enforce scope filter FR-008)
        t_recall_start = time.monotonic()
        dense_results, sparse_results = self._qdrant_store.query_hybrid(
            collection=collection_name,
            dense_vector=query_vector,
            sparse_vector=sparse_query,
            scope_ids=scope_ids,
            version_id=None,
            limit=limit,
        )
        recall_elapsed = (time.monotonic() - t_recall_start) * 1000

        # RRF fusion (FR-003, deterministic tie-breaker FR-017)
        t_fusion_start = time.monotonic()
        fused = rrf_fuse(dense_results, sparse_results, k=60)
        fusion_elapsed = (time.monotonic() - t_fusion_start) * 1000

        # Build chunk_id -> result map from both dense and sparse results
        result_map: dict[str, dict[str, Any]] = {}
        for r in dense_results + sparse_results:
            payload = r.get("payload") or {}
            chunk_id = str(payload.get("chunk_id", r.get("id", "")))
            if chunk_id and chunk_id not in result_map:
                result_map[chunk_id] = r

        # Convert fused candidates to raw_results format (sorted by fused score)
        raw_results: list[dict[str, Any]] = []
        for cand in fused:
            r = result_map.get(cand.chunk_id)
            if r is not None:
                raw_results.append({
                    "id": r.get("id"),
                    "score": cand.fused_score,
                    "payload": r.get("payload", {}),
                })

        failed_paths: list[str] = []

        # Rerank (FR-004/FR-005): only if reranker is configured and results exist
        rerank_elapsed = 0.0
        if self._reranker is not None and raw_results:
            rerank_budget = self._settings.hybrid_retrieval.rerank_budget
            # Trim to rerank budget (FR-005, blueprint §18.5)
            rerank_input = raw_results[:rerank_budget]

            # Fetch chunk content_text for rerank (query+passage pairs)
            chunk_ids: list[int] = []
            for r in rerank_input:
                cid = r.get("payload", {}).get("chunk_id")
                if cid is not None:
                    try:
                        chunk_ids.append(int(cid))
                    except (ValueError, TypeError):
                        pass

            content_map = await self._fetch_chunk_content(chunk_ids)

            candidates = [
                {
                    "chunk_id": str(r.get("payload", {}).get("chunk_id", "")),
                    "content_text": content_map.get(
                        int(r.get("payload", {}).get("chunk_id", 0)), ""
                    ),
                    "fused_score": r.get("score", 0.0),
                }
                for r in rerank_input
            ]

            try:
                t_rerank_start = time.monotonic()
                reranked = await self._reranker.rerank(
                    query, candidates, top_k=limit,
                )
                rerank_elapsed = (time.monotonic() - t_rerank_start) * 1000

                # Rebuild raw_results from reranked order
                reranked_map: dict[str, dict[str, Any]] = {}
                for r in reranked:
                    cid = str(r.get("chunk_id", ""))
                    if cid:
                        reranked_map[cid] = r

                new_raw: list[dict[str, Any]] = []
                for r in reranked:
                    cid = str(r.get("chunk_id", ""))
                    orig = result_map.get(cid, {})
                    new_raw.append({
                        "id": orig.get("id"),
                        "score": r.get("rerank_score", r.get("fused_score", 0.0)),
                        "payload": orig.get("payload", {}),
                    })
                raw_results = new_raw

            except Exception as rerank_exc:
                logger.warning("Rerank failed, using RRF results: %s", rerank_exc)
                failed_paths.append("rerank_failed")

        subpath_timings = {
            "dense_recall_ms": round(recall_elapsed, 2),
            "sparse_recall_ms": round(recall_elapsed, 2),  # parallel
            "fusion_ms": round(fusion_elapsed, 4),
            "rerank_ms": round(rerank_elapsed, 2),
            "total_ms": round(recall_elapsed + fusion_elapsed + rerank_elapsed, 2),
        }

        logger.info(
            "Hybrid recall: dense=%d sparse=%d fused=%d rerank=%dms, failed=%s, timings=%s",
            len(dense_results), len(sparse_results), len(raw_results),
            rerank_elapsed, failed_paths, subpath_timings,
        )

        return raw_results, subpath_timings, failed_paths

    async def _fetch_chunk_content(self, chunk_ids: list[int]) -> dict[int, str]:
        """Fetch content_text for chunks by ID (for rerank query+passage pairs)."""
        if not chunk_ids:
            return {}
        result = await self._session.execute(
            select(Chunk.chunk_id, Chunk.content_text).where(
                Chunk.chunk_id.in_(chunk_ids)
            )
        )
        return {row[0]: row[1] for row in result.all()}

    async def _build_sparse_encoder(self, scope_ids: list[int]) -> BM25SparseEncoder | None:
        """Build or retrieve a cached BM25SparseEncoder for the given scopes.

        Uses a module-level cache keyed by sorted scope_ids to avoid a full DB
        scan + fit() on every hybrid search call (T036, FR-015/SC-005).

        Hash-based term IDs ensure the cached encoder produces compatible IDs
        with stored sparse vectors regardless of when it was built.
        """
        cache_key = tuple(sorted(scope_ids))
        cached = _sparse_encoder_cache.get(cache_key)
        if cached is not None:
            logger.debug("Sparse encoder cache hit for scopes %s", cache_key)
            return cached

        logger.debug("Sparse encoder cache miss for scopes %s — building", cache_key)
        result = await self._session.execute(
            select(Chunk.content_text)
            .join(KnowledgeVersion, Chunk.version_id == KnowledgeVersion.version_id)
            .where(
                Chunk.knowledge_scope_id.in_(scope_ids),
                KnowledgeVersion.status == "published",
            )
            .order_by(Chunk.chunk_id)
        )
        texts = [row[0] for row in result.all()]
        if not texts:
            return None

        encoder = BM25SparseEncoder()
        encoder.fit(texts)
        _sparse_encoder_cache[cache_key] = encoder
        logger.info("Sparse encoder cached for scopes %s (%d terms)", cache_key, encoder.vocab_size)
        return encoder

    async def _record_retrieval_run(
        self,
        query: str,
        project_scopes: list[str],
        completion_status: str,
        evidence_count: int,
        duration_ms: int,
        retrieval_mode: str = "dense",
        subpath_timings: dict | None = None,
        evidence_ref_ids: list[str] | None = None,
        format: str | None = None,
    ) -> None:
        """Record an append-only RetrievalRun audit entry (003 adds format, FR-027)."""
        try:
            run = RetrievalRun(
                run_id=generate_id(),
                query_text=query,
                project_scopes=project_scopes,
                completion_status=completion_status,
                evidence_count=evidence_count,
                duration_ms=duration_ms,
                retrieval_mode=retrieval_mode,
                subpath_timings=subpath_timings,
                evidence_ref_ids=evidence_ref_ids or [],
                format=format,
            )
            self._session.add(run)
            await self._session.flush()
        except Exception:
            # Audit failures must never break the search response
            logger.error("Failed to record RetrievalRun", exc_info=True)

    def _derive_index_version(self) -> str:
        """Derive the Qdrant collection index_version from settings."""
        embedding_model = self._settings.embedding_model
        short_name = embedding_model.rsplit("/", 1)[-1]
        return f"{short_name}_v1"
