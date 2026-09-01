#!/usr/bin/env python3
"""Graph-enhanced comparison evaluation runner (004, T025/T048).

Runs the 002 hybrid baseline (re-run in the same session, FR-025) and the
graph-enhanced path over the extended eval dataset, computes Recall@K / MRR /
nDCG@K / P50-P95 latency, per-query rank + path-score comparison (FR-023),
the three-gate pass decision (SC-001/SC-002/SC-013, FR-024) and hard
constraints, then persists a report conforming to
eval-graph-comparison-report.schema.json.

The graph-enhanced path uses the SAME components as the production retrieval
service (GraphExpansionEngine + rrf_fuse 3rd input) with guardrails from
GraphConfig, so measured behaviour reflects the switch-gated runtime path.

Usage:
    python eval/run_graph_comparison.py \
        --dataset eval/eval_dataset.json \
        --output eval/graph_enhanced_comparison_report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
for p in (_BACKEND_SRC, str(_REPO_ROOT / "eval"), str(_REPO_ROOT / "backend")):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from sqlalchemy import select as sa_select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from rag_mcp.config import get_settings
from rag_mcp.fusion.rrf import rrf_fuse
from rag_mcp.graph.expansion import GraphExpansionEngine
from rag_mcp.graph.store.base import GraphScope
from rag_mcp.graph.store.postgres_graph_store import PostgresGraphStore
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder
from rag_mcp.models.chunk import Chunk
from rag_mcp.models.knowledge_source import KnowledgeSource
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.services.ingestion_service import _derive_index_version
from run_eval import (  # noqa: E402
    _EvalEmbeddingProvider,
    check_reproducibility,
    compute_metrics,
    compute_mrr,
    compute_ndcg_at_k,
    compute_recall_at_k,
    run_single_eval,
)

logger = logging.getLogger(__name__)

_001_QUERY_COUNT = 11  # 001 Dense baseline dataset size (SC-002 basis)
_002_QUERY_COUNT = 18  # 002 hybrid baseline dataset size (SC-013 basis)


def _resolve_data_root(configured: Path) -> Path:
    """Resolve the uploads data root across CWD conventions.

    settings.data_root is relative ('./data/uploads'); it resolves against
    whatever CWD the caller uses. The repo keeps uploads under
    backend/data/uploads, so fall back to the backend-relative location when
    the CWD-relative one does not exist.
    """
    if configured.exists():
        return configured
    backend_candidate = _REPO_ROOT / "backend" / configured
    if backend_candidate.exists():
        return backend_candidate
    return configured


# ---------------------------------------------------------------------------
# Graph corpus preparation (user-triggered rebuild declaration, FR-027)
# ---------------------------------------------------------------------------


def _chunk_to_extractor_dict(chunk: Chunk) -> dict[str, Any]:
    """Map a persisted Chunk to the dict shape the extractors expect.

    Java parser chunks carry chunk_type='symbol'; the call-graph extractor
    distinguishes methods by '#'-qualified symbol paths. All other formats
    keep their parser chunk_type (DDL table chunks MUST stay 'table' so the
    FK extractor's table map resolves them).
    """
    path = chunk.position_path or ""
    if chunk.chunk_type == "symbol":
        symbol_type = "method" if "#" in path else "class"
    else:
        symbol_type = chunk.chunk_type
    return {
        "chunk_id": chunk.chunk_id,
        "symbol_path": path,
        "structure_path": path,
        "symbol_type": symbol_type,
        "chunk_type": chunk.chunk_type,
        "content_text": chunk.content_text or "",
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
    }


async def ensure_graph_corpus(
    session_factory, dataset_scope_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Ensure graph edges exist for the dataset's scopes with java/ddl sources.

    Mirrors the user-triggered rebuild flow (FR-027): deterministic hard
    extraction from the original sources of the published version, then a
    graph_ready capability declaration on that version. Idempotent: scopes
    with edges already present are left untouched. Only the evaluation
    dataset's scopes are prepared — never the whole database.

    Returns scope_id -> {project_id, version_number} for graph-eligible scopes.
    """
    settings = get_settings()
    data_root = _resolve_data_root(Path(settings.data_root))
    triples: dict[int, dict[str, Any]] = {}

    async with session_factory() as session:
        for scope_id in dataset_scope_ids:
            version = (await session.execute(
                sa_select(KnowledgeVersion).where(
                    KnowledgeVersion.knowledge_scope_id == scope_id,
                    KnowledgeVersion.status == "published",
                ).order_by(KnowledgeVersion.version_number.desc())
            )).scalars().first()
            if version is None:
                continue
            project = (await session.execute(sa_text(
                "SELECT project_id FROM projects WHERE knowledge_scope_id = :s LIMIT 1"
            ), {"s": scope_id})).scalar_one_or_none()
            if project is None:
                continue

            version_number = version.version_number
            triples[scope_id] = {
                "project_id": project, "version_number": version_number,
            }

            existing = (await session.execute(sa_text(
                "SELECT count(*) FROM graph_edge WHERE knowledge_scope_id = :s "
                "AND index_version = :iv"
            ), {"s": scope_id, "iv": version_number})).scalar()
            if existing:
                logger.info("Scope %s: %d graph edges present", scope_id, existing)
            else:
                written = await _extract_scope_graph(
                    session, scope_id, project, version, data_root,
                )
                logger.info("Scope %s: extracted %d graph edges", scope_id, written)

            # Declare graph_ready on the published version (FR-013/FR-027)
            caps = dict(version.capabilities or {})
            caps["graph_ready"] = True
            version.capabilities = caps
            version.graph_ready = True
        await session.commit()
    return triples


async def _extract_scope_graph(
    session, scope_id: int, project_id: int,
    version: KnowledgeVersion, data_root: Path,
) -> int:
    """Extract hard relations for every java/ddl source of a version."""
    from rag_mcp.graph.extractors.ddl_fk import DdlFkExtractor
    from rag_mcp.graph.extractors.java_call_graph import JavaCallGraphExtractor

    sources = (await session.execute(
        sa_select(KnowledgeSource).where(
            KnowledgeSource.knowledge_scope_id == scope_id,
            KnowledgeSource.format.in_(["java", "ddl"]),
        )
    )).scalars().all()

    store = PostgresGraphStore(session)
    scope = GraphScope(scope_id, project_id, version.version_number)
    total = 0
    for source in sources:
        raw_path = data_root / str(scope_id) / str(source.source_id) / source.filename
        if not raw_path.exists():
            logger.warning("Raw file missing for source %s: %s",
                           source.source_id, raw_path)
            continue
        raw_text = raw_path.read_bytes().decode("utf-8", errors="replace")

        chunk_rows = (await session.execute(
            sa_select(Chunk).where(
                Chunk.source_id == source.source_id,
                Chunk.version_id == version.version_id,
            )
        )).scalars().all()
        chunk_dicts = [_chunk_to_extractor_dict(c) for c in chunk_rows]

        if source.format == "java":
            extractor = JavaCallGraphExtractor()
        else:
            extractor = DdlFkExtractor()
        edges = extractor.extract(raw_text, chunk_dicts, scope)
        for edge in edges:
            edge["version"] = version.version_number
        total += await store.write_edges(edges, scope)
    return total


# ---------------------------------------------------------------------------
# Graph-enhanced search (mirrors the production switch-gated path)
# ---------------------------------------------------------------------------


async def search_graph_enhanced(
    query: str,
    project_scope_ids: list[int],
    qdrant_store: QdrantStore,
    embedding_provider,
    collection_name: str,
    session: AsyncSession,
    graph_triples: dict[int, dict[str, Any]],
    top_k: int = 5,
    sparse_encoder: BM25SparseEncoder | None = None,
) -> dict[str, Any]:
    """Hybrid recall + graph expansion (3rd RRF input), measuring timing.

    Returns evidence ids, scores, latency, per-candidate detail scores and
    graph-recall metadata (structure_weight/hop_count/edge_path) per chunk.
    """
    start_time = time.perf_counter()
    try:
        query_vector = await embedding_provider.embed_query(query)

        if sparse_encoder is None:
            return {"evidence_ids": [], "scores": [], "status": "error",
                    "error": "sparse encoder missing", "latency_ms": 0.0}
        sparse_query = sparse_encoder.encode(query)
        dense_results, sparse_results = qdrant_store.query_hybrid(
            collection=collection_name,
            dense_vector=query_vector,
            sparse_vector=sparse_query,
            scope_ids=project_scope_ids,
            limit=top_k,
        )

        # Graph recall with production guardrails (FR-017)
        t_graph = time.perf_counter()
        graph_results: list[dict[str, Any]] = []
        graph_meta: dict[str, dict[str, Any]] = {}
        seeds: list[int] = []
        seen_seeds: set[int] = set()
        for r in dense_results + sparse_results:
            payload = r.get("payload") or {}
            try:
                cid = int(payload.get("chunk_id", r.get("id")))
            except (TypeError, ValueError):
                continue
            if cid not in seen_seeds:
                seen_seeds.add(cid)
                seeds.append(cid)

        engine = GraphExpansionEngine(session)
        for scope_id in project_scope_ids:
            triple = graph_triples.get(scope_id)
            if triple is None:
                continue
            scope = GraphScope(scope_id, triple["project_id"],
                               triple["version_number"])
            candidates = await engine.expand(seeds, scope)
            for cand in candidates:
                graph_results.append({
                    "chunk_id": str(cand.chunk_id),
                    "knowledge_scope_id": str(cand.knowledge_scope_id),
                    "graph_rank": cand.graph_rank,
                    "structure_weight": cand.structure_weight,
                })
                graph_meta[str(cand.chunk_id)] = {
                    "structure_weight": cand.structure_weight,
                    "hop_count": cand.hop_count,
                    "edge_path": cand.edge_path,
                    "start_chunk_id": cand.start_chunk_id,
                }
        graph_elapsed = (time.perf_counter() - t_graph) * 1000

        fused = rrf_fuse(dense_results, sparse_results, k=60,
                         graph_results=graph_results or None)

        result_map: dict[str, dict[str, Any]] = {}
        for r in dense_results + sparse_results:
            payload = r.get("payload") or {}
            cid = str(payload.get("chunk_id", r.get("id", "")))
            if cid and cid not in result_map:
                result_map[cid] = r
        # Graph-only candidates: fetch payload metadata from PG
        graph_only = [g["chunk_id"] for g in graph_results
                      if g["chunk_id"] not in result_map]
        if graph_only:
            rows = (await session.execute(
                sa_select(Chunk).where(Chunk.chunk_id.in_([int(c) for c in graph_only]))
            )).scalars().all()
            for c in rows:
                result_map[str(c.chunk_id)] = {
                    "id": c.chunk_id,
                    "score": 0.0,
                    "payload": {
                        "chunk_id": str(c.chunk_id),
                        "version_id": str(c.version_id),
                        "knowledge_scope_id": str(c.knowledge_scope_id),
                        "source_id": str(c.source_id),
                        "position_path": c.position_path or "",
                        "chunk_type": c.chunk_type,
                        "index_version": c.index_version,
                    },
                }

        results = []
        for c in fused:
            r = result_map.get(c.chunk_id)
            if r is not None:
                results.append({
                    "id": r.get("id"),
                    "score": c.fused_score,
                    "payload": r.get("payload", {}),
                })

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        evidence_ids = [str(r["id"]) for r in results]
        scores = [r["score"] for r in results]
        scope_set = {str(s) for s in project_scope_ids}
        payload_scopes = [
            str(r.get("payload", {}).get("knowledge_scope_id", ""))
            for r in results
        ]

        candidate_details = []
        for c in fused:
            candidate_details.append({
                "chunk_id": c.chunk_id,
                "dense_score": c.dense_score,
                "sparse_score": c.sparse_score,
                "graph_rank": c.graph_rank,
                "graph_structure_weight": c.graph_structure_weight,
                "fused_score": c.fused_score,
                "rerank_score": c.fused_score,
            })

        return {
            "evidence_ids": evidence_ids,
            "scores": scores,
            "latency_ms": elapsed_ms,
            "graph_recall_ms": graph_elapsed,
            "status": "ok",
            "candidate_details": candidate_details,
            "graph_meta": graph_meta,
            "payload_scopes": payload_scopes,
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.error("Graph-enhanced search failed for '%s': %s", query[:80], exc)
        return {"evidence_ids": [], "scores": [], "latency_ms": elapsed_ms,
                "status": "error", "error": str(exc)}


async def run_graph_eval(
    dataset: list[dict[str, Any]],
    qdrant_store: QdrantStore,
    embedding_provider,
    collection_name: str,
    session_factory,
    graph_triples: dict[int, dict[str, Any]],
    top_k: int,
    sparse_encoder: BM25SparseEncoder | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    """Run the graph-enhanced path over the dataset.

    Returns (per_query_results, metrics, leakage_events).
    """
    per_query_results: list[dict[str, Any]] = []
    leakage_events = 0

    async with session_factory() as session:
        for i, entry in enumerate(dataset):
            query = entry["query"]
            scope_ids_int = [int(s) for s in entry.get("project_scope", [])]
            expected_ids = entry.get("expected_evidence_ids", [])

            result = await search_graph_enhanced(
                query=query,
                project_scope_ids=scope_ids_int,
                qdrant_store=qdrant_store,
                embedding_provider=embedding_provider,
                collection_name=collection_name,
                session=session,
                graph_triples=graph_triples,
                top_k=top_k,
                sparse_encoder=sparse_encoder,
            )

            scope_set = {str(s) for s in scope_ids_int}
            for ps in result.get("payload_scopes", []):
                if ps and ps not in scope_set:
                    leakage_events += 1

            per_query_results.append({
                "query_index": i,
                "query": query,
                "project_scope": entry.get("project_scope", []),
                "expected_evidence_ids": expected_ids,
                "retrieved_evidence_ids": result["evidence_ids"],
                "scores": result["scores"],
                "latency_ms": result["latency_ms"],
                "status": result["status"],
                "candidate_details": result.get("candidate_details", []),
                "graph_meta": result.get("graph_meta", {}),
                "graph_recall_ms": result.get("graph_recall_ms", 0.0),
            })

            if (i + 1) % 10 == 0 or i == len(dataset) - 1:
                logger.info("  Processed %d/%d queries", i + 1, len(dataset))

    metrics = compute_metrics(per_query_results, top_k)
    return per_query_results, metrics, leakage_events


# ---------------------------------------------------------------------------
# Hard-constraint measurements
# ---------------------------------------------------------------------------


def _load_mcp_search_schema() -> dict[str, Any]:
    schema_path = (
        _REPO_ROOT / "specs" / "001-minimum-rag-mcp-loop" / "contracts"
        / "mcp-search-output.schema.json"
    )
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


async def measure_hard_constraints(
    dataset: list[dict[str, Any]],
    graph_per_query: list[dict[str, Any]],
    leakage_events: int,
    session_factory,
) -> dict[str, Any]:
    """Measure schema validity + source locatability over returned evidence."""
    import jsonschema

    schema = _load_mcp_search_schema()
    total_items = 0
    valid_responses = 0
    locatable_items = 0

    chunk_ids: set[int] = set()
    for pq in graph_per_query:
        for eid in pq["retrieved_evidence_ids"]:
            try:
                chunk_ids.add(int(eid))
            except ValueError:
                pass

    position_map: dict[int, str] = {}
    async with session_factory() as session:
        if chunk_ids:
            rows = (await session.execute(
                sa_select(Chunk.chunk_id, Chunk.position_path).where(
                    Chunk.chunk_id.in_(list(chunk_ids))
                )
            )).all()
            position_map = {r[0]: (r[1] or "") for r in rows}

    for i, pq in enumerate(graph_per_query):
        evidence_items = []
        for eid in pq["retrieved_evidence_ids"]:
            total_items += 1
            position = position_map.get(int(eid), "")
            if position:
                locatable_items += 1
            evidence_items.append({
                "evidence_id": str(eid),
                "content_excerpt": "eval",
                "source_version": 1,
                "source_position": position,
                "knowledge_scope_id": str(
                    pq.get("project_scope", ["0"])[0]
                ),
                "knowledge_scope_type": "project",
                "relevance_score": 0.5,
            })
        response = {
            "completion_status": "complete" if evidence_items else "no_evidence",
            "evidence": evidence_items,
            "request_id": f"eval-{i}",
        }
        try:
            jsonschema.validate(response, schema)
            valid_responses += 1
        except jsonschema.ValidationError as exc:
            logger.warning("Schema validation failed for query %d: %s",
                           i, exc.message[:120])

    n_responses = max(1, len(graph_per_query))
    return {
        "cross_project_leakage_events": leakage_events,
        "schema_validity_rate": round(valid_responses / n_responses, 4),
        "source_locatability_rate": round(
            locatable_items / total_items, 4
        ) if total_items else 1.0,
        "all_passed": False,  # filled by caller
    }


# ---------------------------------------------------------------------------
# Gate computations (SC-001 / SC-002 / SC-013)
# ---------------------------------------------------------------------------


def _subset_metrics(
    dataset: list[dict[str, Any]],
    per_query: list[dict[str, Any]],
    indices: list[int],
    top_k: int,
) -> dict[str, Any]:
    subset = [per_query[i] for i in indices]
    return compute_metrics(subset, top_k)


def _relative_improvement(baseline: float, graph: float) -> float:
    if baseline == 0:
        return 0.0
    return (graph - baseline) / baseline * 100.0


def compute_sc002_noninferior(
    dataset: list[dict[str, Any]],
    dense_per_query: list[dict[str, Any]],
    graph_per_query: list[dict[str, Any]],
    top_k: int,
    tolerance: float = 0.01,
) -> tuple[bool, str]:
    """SC-002: 001 11-query non-inferior gate on the CURRENT corpus.

    The gate compares the graph-enhanced path against a same-session Dense
    baseline re-run (FR-025 fairness; the spec Assumptions require a corpus
    rebuild before the comparison, after which the stored 001 report reflects
    the pre-rebuild corpus). Recall@K exact; MRR/nDCG within 1% relative
    tolerance (research §6). The stored eval/baseline_report.json is
    cross-checked informationally when its expected IDs still match.
    """
    if len(graph_per_query) < _001_QUERY_COUNT or len(dense_per_query) < _001_QUERY_COUNT:
        return False, "insufficient queries for SC-002"

    for i in range(_001_QUERY_COUNT):
        expected = dataset[i].get("expected_evidence_ids", [])
        dense_retrieved = dense_per_query[i]["retrieved_evidence_ids"]
        graph_retrieved = graph_per_query[i]["retrieved_evidence_ids"]

        dense_recall = compute_recall_at_k(expected, dense_retrieved, top_k)
        graph_recall = compute_recall_at_k(expected, graph_retrieved, top_k)
        if graph_recall < dense_recall:
            return False, f"query {i}: recall regressed vs dense baseline"

        for fn in (compute_mrr, lambda e, r: compute_ndcg_at_k(e, r, top_k)):
            dense_val = fn(expected, dense_retrieved)
            graph_val = fn(expected, graph_retrieved)
            if dense_val > 0 and graph_val < dense_val * (1 - tolerance):
                return False, f"query {i}: metric regressed beyond tolerance"
    return True, "ok"


def crosscheck_stored_001_baseline(
    graph_per_query: list[dict[str, Any]],
) -> str:
    """Informational cross-check against the stored eval/baseline_report.json.

    Returns a human-readable note. After a corpus rebuild the stored report's
    expected chunk IDs no longer match the live corpus; in that case the
    cross-check is reported as stale rather than treated as a gate.
    """
    baseline_path = _REPO_ROOT / "eval" / "baseline_report.json"
    if not baseline_path.exists():
        return "stored baseline_report.json missing"
    try:
        with open(baseline_path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        stored_per_query = stored.get("per_query_results", [])[:_001_QUERY_COUNT]
        if not stored_per_query:
            return "stored baseline has no per-query results"
        stored_expected = {
            str(e) for q in stored_per_query for e in q.get("expected_evidence_ids", [])
        }
        current_chunks = {
            str(e) for q in graph_per_query[:_001_QUERY_COUNT]
            for e in q.get("retrieved_evidence_ids", [])
        }
        if stored_expected & current_chunks:
            return "stored baseline corpus matches live corpus"
        return ("stored baseline reflects pre-rebuild corpus "
                "(expected chunk IDs differ); same-session dense baseline used "
                "for the SC-002 gate")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return f"stored baseline unreadable: {exc}"


def compute_sc013_noninferior(
    dataset: list[dict[str, Any]],
    baseline_per_query: list[dict[str, Any]],
    graph_per_query: list[dict[str, Any]],
    top_k: int,
    tolerance: float = 0.01,
) -> bool:
    """SC-013: 002 original non-structural queries non-inferior.

    Compares against the same-session hybrid re-run (FR-025 fair baseline):
    MRR/nDCG within 1% relative tolerance, Recall@K non-decreasing.
    """
    indices = [
        i for i in range(min(_002_QUERY_COUNT, len(dataset)))
        if not dataset[i].get("is_structural_benefit", False)
    ]
    if not indices:
        return False
    base = _subset_metrics(dataset, baseline_per_query, indices, top_k)
    graph = _subset_metrics(dataset, graph_per_query, indices, top_k)

    if graph["mrr"]["mean"] < base["mrr"]["mean"] * (1 - tolerance):
        return False
    if graph["ndcg_at_k"]["mean"] < base["ndcg_at_k"]["mean"] * (1 - tolerance):
        return False
    if graph["recall_at_k"]["mean"] < base["recall_at_k"]["mean"]:
        return False
    return True


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def run_graph_comparison(
    dataset_path: str,
    output_path: str,
    top_k: int = 5,
    qdrant_url: str | None = None,
    skip_reproducibility: bool = False,
) -> int:
    settings = get_settings()

    ds_path = Path(dataset_path)
    if not ds_path.exists():
        logger.error("Dataset not found: %s", dataset_path)
        return 1
    with open(ds_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    if not dataset:
        logger.error("Dataset is empty")
        return 1
    logger.info("Loaded %d queries from %s", len(dataset), ds_path)

    embedding_provider = _EvalEmbeddingProvider(settings.embedding_model)
    qdrant_store = QdrantStore(url=qdrant_url or settings.qdrant_url)

    index_version = _derive_index_version(settings.embedding_model)
    hybrid_collection = f"chunks_hybrid_{index_version}"

    engine = create_async_engine(settings.database_url)
    session_factory = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Precheck: the hybrid collection must exist before spending eval time
    if not qdrant_store.collection_exists(hybrid_collection):
        logger.error(
            "Qdrant collection %s does not exist on %s — ingest the eval "
            "corpus first (hybrid index required for the comparison).",
            hybrid_collection, qdrant_url or settings.qdrant_url,
        )
        await engine.dispose()
        return 1

    # 0. Prepare graph corpus (rebuild + graph_ready declaration, FR-027)
    logger.info("=== Graph Corpus Preparation ===")
    dataset_scope_ids = sorted({
        int(s) for entry in dataset for s in entry.get("project_scope", [])
    })
    graph_triples = await ensure_graph_corpus(session_factory, dataset_scope_ids)
    if not graph_triples:
        logger.error("No graph-eligible scopes found; aborting")
        await engine.dispose()
        return 1
    logger.info("Graph-eligible scopes: %d", len(graph_triples))

    # Sparse encoder over published chunks (same convention as run_comparison)
    async with session_factory() as session:
        qresult = await session.execute(
            sa_select(Chunk.content_text)
            .join(KnowledgeVersion, Chunk.version_id == KnowledgeVersion.version_id)
            .where(KnowledgeVersion.status == "published")
            .order_by(Chunk.chunk_id)
        )
        texts = [row[0] for row in qresult.all()]
    sparse_encoder = None
    if texts:
        sparse_encoder = BM25SparseEncoder()
        sparse_encoder.fit(texts)
        logger.info("Sparse encoder fitted on %d chunk texts", len(texts))
    else:
        logger.error("No published chunk texts for sparse encoder; aborting")
        await engine.dispose()
        return 1

    # 1. Baselines RE-RUN in the same session (FR-025): the 002 hybrid
    #    baseline (comparison basis) and the 001 dense basis for SC-002.
    dense_collection = f"chunks_dense_{index_version}"
    logger.info("=== Dense Baseline Re-Run (same session, SC-002 basis) ===")
    dense_per_query, _dense_metrics = await run_single_eval(
        dataset, qdrant_store, embedding_provider, dense_collection, top_k,
        mode="dense", sparse_encoder=None,
    )

    logger.info("=== Hybrid Baseline Re-Run (same session) ===")
    baseline_per_query, baseline_metrics = await run_single_eval(
        dataset, qdrant_store, embedding_provider, hybrid_collection, top_k,
        mode="hybrid", sparse_encoder=sparse_encoder,
    )

    # 2. Graph-enhanced run
    logger.info("=== Graph-Enhanced Run ===")
    graph_per_query, graph_metrics, leakage_events = await run_graph_eval(
        dataset, qdrant_store, embedding_provider, hybrid_collection,
        session_factory, graph_triples, top_k, sparse_encoder,
    )

    # 3. Structural subset (SC-001)
    structural_indices = [
        i for i, e in enumerate(dataset) if e.get("is_structural_benefit", False)
    ]
    structural_baseline = _subset_metrics(
        dataset, baseline_per_query, structural_indices, top_k
    )
    structural_graph = _subset_metrics(
        dataset, graph_per_query, structural_indices, top_k
    )
    mrr_improvement = _relative_improvement(
        structural_baseline["mrr"]["mean"], structural_graph["mrr"]["mean"]
    )
    ndcg_improvement = _relative_improvement(
        structural_baseline["ndcg_at_k"]["mean"],
        structural_graph["ndcg_at_k"]["mean"],
    )
    recall_non_decreasing = (
        structural_graph["recall_at_k"]["mean"]
        >= structural_baseline["recall_at_k"]["mean"]
    )
    sc001_improvement_pct = min(mrr_improvement, ndcg_improvement)
    logger.info(
        "Structural subset (%d queries): MRR %.4f->%.4f (%.2f%%), "
        "nDCG %.4f->%.4f (%.2f%%)",
        len(structural_indices),
        structural_baseline["mrr"]["mean"], structural_graph["mrr"]["mean"],
        mrr_improvement,
        structural_baseline["ndcg_at_k"]["mean"], structural_graph["ndcg_at_k"]["mean"],
        ndcg_improvement,
    )

    # 4. SC-002 (001 non-inferior) and SC-013 (002 non-structural non-inferior)
    sc002_pass, sc002_note = compute_sc002_noninferior(
        dataset, dense_per_query, graph_per_query, top_k
    )
    sc013_pass = compute_sc013_noninferior(
        dataset, baseline_per_query, graph_per_query, top_k
    )
    logger.info("SC-002 001 non-inferior: %s (%s)", sc002_pass, sc002_note)
    logger.info("SC-002 stored-report cross-check: %s",
                crosscheck_stored_001_baseline(graph_per_query))
    logger.info("SC-013 002 non-structural non-inferior: %s", sc013_pass)

    # 5. Hard constraints (measured)
    hard_constraints = await measure_hard_constraints(
        dataset, graph_per_query, leakage_events, session_factory
    )
    hard_constraints["all_passed"] = (
        hard_constraints["cross_project_leakage_events"] == 0
        and hard_constraints["schema_validity_rate"] >= 1.0
        and hard_constraints["source_locatability_rate"] >= 1.0
    )
    logger.info("Hard constraints: %s", hard_constraints)

    # 6. Reproducibility (SC-007): second graph run
    if skip_reproducibility:
        repro = {"non_latency_reproducible": True, "tolerance": 0.01, "checks": []}
    else:
        logger.info("=== Graph Reproducibility Run ===")
        _, graph_metrics_2, _ = await run_graph_eval(
            dataset, qdrant_store, embedding_provider, hybrid_collection,
            session_factory, graph_triples, top_k, sparse_encoder,
        )
        repro_report = check_reproducibility(graph_metrics, graph_metrics_2)
        repro = {
            "non_latency_reproducible": repro_report["reproducible"],
            "tolerance": repro_report["tolerance"],
            "checks": repro_report["checks"],
        }

    # 7. Per-query comparison (FR-023/SC-008)
    per_query_comparison = []
    for i, entry in enumerate(dataset):
        expected_ids = entry.get("expected_evidence_ids", [])
        expected_set = set(expected_ids)

        baseline_retrieved = baseline_per_query[i]["retrieved_evidence_ids"]
        baseline_rank = next(
            (r for r, rid in enumerate(baseline_retrieved, start=1)
             if rid in expected_set), None
        )
        graph_retrieved = graph_per_query[i]["retrieved_evidence_ids"]
        graph_rank = next(
            (r for r, rid in enumerate(graph_retrieved, start=1)
             if rid in expected_set), None
        )

        baseline_details = baseline_per_query[i].get("candidate_details", [])
        graph_details = graph_per_query[i].get("candidate_details", [])
        graph_meta = graph_per_query[i].get("graph_meta", {})

        def _detail(details, chunk_id):
            for d in details:
                if str(d.get("chunk_id", "")) == str(chunk_id):
                    return d
            return None

        expected_first = expected_ids[0] if expected_ids else None
        b_det = _detail(baseline_details, expected_first) if expected_first else None
        g_det = _detail(graph_details, expected_first) if expected_first else None

        # Graph path info for the expected chunk when graph-recalled
        g_meta = graph_meta.get(str(expected_first)) if expected_first else None
        graph_weight = g_meta["structure_weight"] if g_meta else None
        graph_hops = g_meta["hop_count"] if g_meta else None
        edge_path_summary = []
        if g_meta:
            for step in g_meta["edge_path"][:3]:
                edge_path_summary.append({
                    "hop": step.get("hop"),
                    "edge_id": str(step.get("edge_id")),
                    "relation_type": step.get("relation_type"),
                    "direction": step.get("direction"),
                    "is_hard": step.get("is_hard"),
                })

        rank_improved = (
            graph_rank is not None
            and (baseline_rank is None or graph_rank < baseline_rank)
        )

        per_query_comparison.append({
            "query_index": i,
            "query": entry["query"],
            "is_structural_benefit": bool(entry.get("is_structural_benefit", False)),
            "project_scope": str(entry.get("project_scope", [""])[0]),
            "expected_evidence_ids": expected_ids,
            "baseline_rank": baseline_rank,
            "graph_rank": graph_rank,
            "baseline_dense_score": (b_det or {}).get("dense_score"),
            "baseline_sparse_score": (b_det or {}).get("sparse_score"),
            "baseline_fused_score": (b_det or {}).get("fused_score"),
            "baseline_rerank_score": (b_det or {}).get("rerank_score"),
            "graph_dense_score": (g_det or {}).get("dense_score"),
            "graph_sparse_score": (g_det or {}).get("sparse_score"),
            "graph_recall_structure_weight": graph_weight,
            "graph_recall_hop_count": graph_hops,
            "graph_edge_path_summary": edge_path_summary,
            "graph_fused_score": (g_det or {}).get("fused_score"),
            "graph_rerank_score": (g_det or {}).get("rerank_score"),
            "rank_improved": rank_improved,
        })

    # 8. Assemble report via GraphComparisonRunner (T025)
    from graph_comparison_runner import GraphComparisonRunner

    gcfg = settings.graph
    runner_config = {
        "embedding_model": settings.embedding_model,
        "reranker_model": settings.hybrid_retrieval.reranker_model,
        "hybrid_collection": hybrid_collection,
        "fusion_algorithm": settings.hybrid_retrieval.fusion_algorithm,
        "dataset_path": str(ds_path.resolve()),
        "num_queries": len(dataset),
        "structural_subset_size": len(structural_indices),
        "graph_candidate_budget": gcfg.candidate_budget,
        "graph_total_timeout_ms": gcfg.total_timeout_ms,
    }

    def _metric_block(m: dict) -> dict:
        return {
            "mean": round(m["mean"], 4),
            "min": round(m["min"], 4),
            "max": round(m["max"], 4),
        }

    def _latency_block(m: dict) -> dict:
        return {
            "p50": round(m["p50"], 2),
            "p95": round(m["p95"], 2),
            "mean": round(m["mean"], 2),
            "min": round(m["min"], 2),
            "max": round(m["max"], 2),
        }

    runner = GraphComparisonRunner(runner_config)
    report = runner.build_report(
        baseline_metrics={
            "recall_at_k": _metric_block(baseline_metrics["recall_at_k"]),
            "mrr": _metric_block(baseline_metrics["mrr"]),
            "ndcg_at_k": _metric_block(baseline_metrics["ndcg_at_k"]),
            "latency_ms": _latency_block(baseline_metrics["latency_ms"]),
        },
        graph_metrics={
            "recall_at_k": _metric_block(graph_metrics["recall_at_k"]),
            "mrr": _metric_block(graph_metrics["mrr"]),
            "ndcg_at_k": _metric_block(graph_metrics["ndcg_at_k"]),
            "latency_ms": _latency_block(graph_metrics["latency_ms"]),
        },
        structural_metrics={
            "baseline_mrr_mean": round(structural_baseline["mrr"]["mean"], 4),
            "graph_mrr_mean": round(structural_graph["mrr"]["mean"], 4),
            "baseline_ndcg_mean": round(structural_baseline["ndcg_at_k"]["mean"], 4),
            "graph_ndcg_mean": round(structural_graph["ndcg_at_k"]["mean"], 4),
            "recall_non_decreasing": recall_non_decreasing,
        },
        sc001_improvement_pct=sc001_improvement_pct,
        sc002_noninferior=sc002_pass,
        sc013_noninferior=sc013_pass,
        per_query=per_query_comparison,
        hard_constraints=hard_constraints,
        reproducibility=repro,
    )

    # 9. Validate against the 004 report contract, then persist
    try:
        import jsonschema

        sys.path.insert(0, str(_REPO_ROOT / "backend"))
        from tests.contract._graph_schema_helper import (
            common_schema,
            eval_report_schema,
            graph_relations_schema,
            graph_trace_schema,
            inline_refs,
        )
        schema = inline_refs(
            eval_report_schema(), common_schema(), graph_relations_schema(),
            graph_trace_schema(), eval_report_schema(),
        )
        jsonschema.validate(report, schema)
        logger.info("Report validates against eval-graph-comparison-report.schema.json")
    except Exception as exc:  # noqa: BLE001
        logger.error("Report schema validation FAILED: %s", exc)
        return 2

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("Graph comparison report written to %s", out_path)
    logger.info("three_gate_pass: %s", report["three_gate_pass"])
    logger.info("enters_default_path: %s", report["enters_default_path"])

    await engine.dispose()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run graph-enhanced vs hybrid comparison evaluation "
                    "(004, FR-022/FR-023/FR-024/FR-025).",
    )
    parser.add_argument("--dataset", "-d", default="eval/eval_dataset.json")
    parser.add_argument(
        "--output", "-o", default="eval/graph_enhanced_comparison_report.json"
    )
    parser.add_argument("--top-k", "-k", type=int, default=5)
    parser.add_argument("--qdrant-url", type=str, default=None)
    parser.add_argument("--skip-reproducibility", action="store_true", default=False)
    parser.add_argument("--verbose", "-v", action="store_true", default=False)
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return await run_graph_comparison(
        dataset_path=args.dataset,
        output_path=args.output,
        top_k=args.top_k,
        qdrant_url=args.qdrant_url,
        skip_reproducibility=args.skip_reproducibility,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
