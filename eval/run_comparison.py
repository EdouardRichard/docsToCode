#!/usr/bin/env python3
"""Comparison evaluation runner: Dense baseline vs Hybrid retrieval (002).

Runs Dense baseline then Hybrid in the same session (FR-024), produces a
comparison report matching eval-comparison-report.schema.json.

Usage:
    python eval/run_comparison.py --dataset eval/eval_dataset.json \
        --output eval/hybrid_comparison_report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from run_eval import (
    _EvalEmbeddingProvider,
    run_single_eval,
    compute_metrics,
    check_reproducibility,
    compute_mrr,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Format expansion report (T039 / FR-026)
# ---------------------------------------------------------------------------

# Java markers for inferring format of original 18 queries (no explicit field)
_JAVA_MARKERS = (
    "com.example", "UserService", "validateToken",
    "DB_PASSWORD", "findById", "getActiveUsers",
)

# Canonical ordering of formats in the per-format report
_FORMAT_ORDER = (
    "java", "markdown", "openapi", "ddl", "go", "python", "word", "pdf",
)


def _infer_query_format(entry: dict[str, Any]) -> str:
    """Infer the retrieval format of a dataset entry.

    New 003 entries carry an explicit format field.  Original 001/002
    entries (18 queries, no format field) are classified as java or
    markdown from query text.
    """
    if "format" in entry:
        return str(entry["format"])
    query = entry.get("query", "")
    if any(m in query for m in _JAVA_MARKERS):
        return "java"
    if "#" in query:
        return "markdown"
    return "unknown"


def _group_results_by_format(
    dataset: list[dict[str, Any]],
    per_query: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group per-query results by the inferred format of their dataset entry."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for i, entry in enumerate(dataset):
        fmt = _infer_query_format(entry)
        groups.setdefault(fmt, []).append(per_query[i])
    return groups


def _flat_metrics(m: dict[str, Any], top_k: int) -> dict[str, Any]:
    """Flatten a compute_metrics() result (or a stored metrics block) into
    the per-format report schema: recall_at_K / mrr / ndcg_at_K / p50 / p95.
    """
    return {
        "num_queries": m.get("num_queries", 0),
        f"recall_at_{top_k}": {
            "mean": round(m.get("recall_at_k", {}).get("mean", 0.0), 4),
            "min": round(m.get("recall_at_k", {}).get("min", 0.0), 4),
            "max": round(m.get("recall_at_k", {}).get("max", 0.0), 4),
        },
        "mrr": {
            "mean": round(m.get("mrr", {}).get("mean", 0.0), 4),
            "min": round(m.get("mrr", {}).get("min", 0.0), 4),
            "max": round(m.get("mrr", {}).get("max", 0.0), 4),
        },
        f"ndcg_at_{top_k}": {
            "mean": round(m.get("ndcg_at_k", {}).get("mean", 0.0), 4),
            "min": round(m.get("ndcg_at_k", {}).get("min", 0.0), 4),
            "max": round(m.get("ndcg_at_k", {}).get("max", 0.0), 4),
        },
        "p50": round(m.get("latency_ms", {}).get("p50", 0.0), 2),
        "p95": round(m.get("latency_ms", {}).get("p95", 0.0), 2),
    }


def _empty_flat_metrics(top_k: int) -> dict[str, Any]:
    """Zero-valued placeholder when a format group has no queries."""
    zero = {"mean": 0.0, "min": 0.0, "max": 0.0}
    return {
        "num_queries": 0,
        f"recall_at_{top_k}": zero,
        "mrr": zero,
        f"ndcg_at_{top_k}": zero,
        "p50": 0.0,
        "p95": 0.0,
    }


def _format_report_target(
    format_report_output: str | None,
    limit: int | None,
) -> Path:
    """Resolve the per-format report path for this run scope (T055).

    - An explicit --format-report always wins.
    - An unlimited run (the 003 full mixed-format scope) targets the declared
      003 artifact eval/format_expansion_report.json.
    - A limited run (the 002 fixed 18-query acceptance scope) targets a
      distinct eval/format_expansion_report_002_limited.json so that
      re-running the 002 acceptance regression can never truncate the 003
      per-format artifact back to the original java/markdown subset.
    """
    if format_report_output:
        return Path(format_report_output)
    if limit is not None and limit > 0:
        return _REPO_ROOT / "eval" / "format_expansion_report_002_limited.json"
    return _REPO_ROOT / "eval" / "format_expansion_report.json"

def generate_format_expansion_report(
    dataset: list[dict[str, Any]],
    dense_per_query: list[dict[str, Any]],
    hybrid_per_query: list[dict[str, Any]],
    dense_metrics: dict[str, Any],
    hybrid_metrics: dict[str, Any],
    top_k: int,
    config: dict[str, Any],
    output_path: str,
    skip_write_when_limited: bool = False,
) -> dict[str, Any]:
    """Generate the per-format comparison report (FR-026 / T039).

    1. Groups eval results by format (explicit field or inferred from query).
    2. Generates per-format Recall@K / MRR / nDCG / P50 / P95 metrics.
    3. Compares 001 Dense baseline vs 002 hybrid baseline vs 003 regression
       (original 18 queries vs newly added queries).
    4. Writes eval/format_expansion_report.json.

    Returns the report dict (also written to output_path).
    """
    # --- 1. Per-format metrics ---
    hybrid_by_format = _group_results_by_format(dataset, hybrid_per_query)
    dense_by_format = _group_results_by_format(dataset, dense_per_query)

    all_formats = list(dict.fromkeys(
        _FORMAT_ORDER + tuple(hybrid_by_format) + tuple(dense_by_format)
    ))

    per_format: dict[str, Any] = {}
    for fmt in all_formats:
        h_queries = hybrid_by_format.get(fmt, [])
        d_queries = dense_by_format.get(fmt, [])
        h_metrics = compute_metrics(h_queries, top_k) if h_queries else None
        d_metrics = compute_metrics(d_queries, top_k) if d_queries else None
        per_format[fmt] = {
            "num_queries": len(h_queries),
            "dense": _flat_metrics(d_metrics, top_k) if d_metrics else _empty_flat_metrics(top_k),
            "hybrid": _flat_metrics(h_metrics, top_k) if h_metrics else _empty_flat_metrics(top_k),
        }

    # --- 2. Regression: original 18 vs newly added queries ---
    n_original = min(18, len(dataset))
    original_dense = compute_metrics(dense_per_query[:n_original], top_k)
    original_hybrid = compute_metrics(hybrid_per_query[:n_original], top_k)
    new_dense = (
        compute_metrics(dense_per_query[n_original:], top_k)
        if len(dense_per_query) > n_original else None
    )
    new_hybrid = (
        compute_metrics(hybrid_per_query[n_original:], top_k)
        if len(hybrid_per_query) > n_original else None
    )

    regression = {
        "original_18": {
            "num_queries": n_original,
            "dense": _flat_metrics(original_dense, top_k),
            "hybrid": _flat_metrics(original_hybrid, top_k),
        },
        "new_12": {
            "num_queries": len(dataset) - n_original,
            "dense": _flat_metrics(new_dense, top_k) if new_dense else _empty_flat_metrics(top_k),
            "hybrid": _flat_metrics(new_hybrid, top_k) if new_hybrid else _empty_flat_metrics(top_k),
        },
    }

    # --- 3. Baseline comparison: 001 Dense vs 002 Hybrid (overall) ---
    baseline_comparison: dict[str, Any] = {
        "001_dense": _flat_metrics(dense_metrics, top_k),
        "002_hybrid": _flat_metrics(hybrid_metrics, top_k),
        "deltas": {
            "recall_mean_delta": round(
                hybrid_metrics["recall_at_k"]["mean"] - dense_metrics["recall_at_k"]["mean"], 6),
            "mrr_mean_delta": round(
                hybrid_metrics["mrr"]["mean"] - dense_metrics["mrr"]["mean"], 6),
            "ndcg_mean_delta": round(
                hybrid_metrics["ndcg_at_k"]["mean"] - dense_metrics["ndcg_at_k"]["mean"], 6),
        },
    }

    # Cross-reference stored 001/002 baseline reports if present
    eval_dir = _REPO_ROOT / "eval"
    baseline_path = eval_dir / "baseline_report.json"
    if baseline_path.exists():
        try:
            with open(baseline_path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            sm = stored.get("metrics")
            if sm:
                baseline_comparison["001_dense_stored"] = _flat_metrics(sm, top_k)
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Could not parse stored baseline_report.json")

    hybrid_path = eval_dir / "hybrid_comparison_report.json"
    if hybrid_path.exists():
        try:
            with open(hybrid_path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            hm = stored.get("hybrid_metrics")
            if hm:
                baseline_comparison["002_hybrid_stored"] = _flat_metrics(hm, top_k)
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Could not parse stored hybrid_comparison_report.json")

    report: dict[str, Any] = {
        "report_type": "format_expansion_comparison",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "per_format": per_format,
        "regression": regression,
        "baseline_comparison": baseline_comparison,
    }

    out = Path(output_path)
    is_declared_003_artifact = out.name == "format_expansion_report.json"
    if skip_write_when_limited and is_declared_003_artifact:
        # T055: a limited (002-scoped) run must never truncate the declared
        # 003 per-format artifact back to the original java/markdown subset;
        # the report is still returned for logging purposes only.
        logger.info(
            "Limited run: skipping write of %s (003 artifact protection)", out,
        )
        return report
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("Format expansion report written to %s", out)

    return report


def _load_search_output_schema() -> dict[str, Any]:
    """Load 001's MCP search output contract for evidence-item validation."""
    path = (
        _REPO_ROOT / "specs" / "001-minimum-rag-mcp-loop" / "contracts"
        / "mcp-search-output.schema.json"
    )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _validate_evidence_item(item: dict[str, Any], schema: dict[str, Any]) -> bool:
    """Validate one evidence item against the MCP output evidence schema."""
    import jsonschema

    try:
        jsonschema.validate(item, schema)
        return True
    except jsonschema.ValidationError:
        return False


async def run_comparison(
    dataset_path: str,
    output_path: str,
    top_k: int = 5,
    qdrant_url: str | None = None,
    format_report_output: str | None = None,
    limit: int | None = None,
) -> int:
    """Run Dense baseline then Hybrid comparison evaluation.

    Returns exit code (0 = success).
    """
    settings = get_settings()

    # Load dataset
    ds_path = Path(dataset_path)
    if not ds_path.exists():
        logger.error("Dataset not found: %s", dataset_path)
        return 1

    with open(ds_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    if not dataset:
        logger.error("Dataset is empty")
        return 1

    # 002 fixed scope: the comparison acceptance set is the original 11 +
    # the 7 lexical-precision queries added by 002 (first 18). Later
    # features (003/004) append entries; --limit keeps the stored record
    # reproducible against the declared 002 set.
    if limit is not None and limit > 0:
        dataset = dataset[:limit]
        logger.info("Limited to first %d queries (002 fixed scope)", len(dataset))

    logger.info("Loaded %d queries from %s", len(dataset), ds_path)

    # Initialize components
    embedding_provider = _EvalEmbeddingProvider(settings.embedding_model)
    qdrant_store = QdrantStore(url=qdrant_url or settings.qdrant_url)

    from rag_mcp.services.ingestion_service import _derive_index_version
    index_version = _derive_index_version(settings.embedding_model)
    dense_collection = f"chunks_dense_{index_version}"
    hybrid_collection = f"chunks_hybrid_{index_version}"

    # Build sparse encoder for hybrid runs (same pattern as run_eval.py)
    sparse_encoder = None
    if True:
        from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder
        from sqlalchemy import select as sa_select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker
        from rag_mcp.models.chunk import Chunk
        from rag_mcp.models.knowledge_version import KnowledgeVersion

        engine = create_async_engine(settings.database_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            qresult = await session.execute(
                sa_select(Chunk.content_text)
                .join(KnowledgeVersion, Chunk.version_id == KnowledgeVersion.version_id)
                .where(KnowledgeVersion.status == "published")
                .order_by(Chunk.chunk_id)
            )
            texts = [row[0] for row in qresult.all()]
        await engine.dispose()
        if texts:
            sparse_encoder = BM25SparseEncoder()
            sparse_encoder.fit(texts)
            logger.info("Sparse encoder fitted on %d chunk texts", len(texts))
        else:
            logger.warning("No chunk texts for sparse encoder, hybrid will fall back to dense")

    # --- Dense baseline run (same session, FR-024) ---
    logger.info("=== Dense Baseline Run ===")
    dense_per_query, dense_metrics = await run_single_eval(
        dataset, qdrant_store, embedding_provider, dense_collection, top_k,
        mode="dense", sparse_encoder=None,
    )

    # --- Hybrid run (reranker wired to mirror the production default
    # path; previously the eval hybrid arm never ran the reranker) ---
    logger.info("=== Hybrid Run ===")
    reranker = None
    try:
        from rag_mcp.providers.local_cpu_reranker import LocalCPUReranker

        reranker = LocalCPUReranker()
        reranker.warmup()
        logger.info("Reranker loaded: %s", settings.hybrid_retrieval.reranker_model)
    except Exception as exc:  # noqa: BLE001 — fusion-only fallback stays honest
        logger.warning("Reranker unavailable, hybrid arm runs fusion-only: %s", exc)
    hybrid_per_query, hybrid_metrics = await run_single_eval(
        dataset, qdrant_store, embedding_provider, hybrid_collection, top_k,
        mode="hybrid", sparse_encoder=sparse_encoder,
        reranker=reranker,
        rerank_budget=settings.hybrid_retrieval.rerank_budget,
    )

    # --- Compute deltas ---
    deltas = {
        "mrr_mean_delta": hybrid_metrics["mrr"]["mean"] - dense_metrics["mrr"]["mean"],
        "ndcg_mean_delta": hybrid_metrics["ndcg_at_k"]["mean"] - dense_metrics["ndcg_at_k"]["mean"],
        "recall_mean_delta": hybrid_metrics["recall_at_k"]["mean"] - dense_metrics["recall_at_k"]["mean"],
        "latency_p50_delta_ms": hybrid_metrics["latency_ms"]["p50"] - dense_metrics["latency_ms"]["p50"],
        "latency_p95_delta_ms": hybrid_metrics["latency_ms"]["p95"] - dense_metrics["latency_ms"]["p95"],
    }

    # --- Hard constraints: MEASURED over the hybrid arm's evidence items
    # (SC-002/SC-003/SC-004). Previously these values were hardcoded
    # literals, so the acceptance record carried no measured evidence. ---
    search_output_schema = _load_search_output_schema()
    evidence_item_schema = search_output_schema["properties"]["evidence"]["items"]
    leakage_events = 0
    locatable = 0
    schema_valid = 0
    evidence_total = 0
    for entry, hybrid_row in zip(dataset, hybrid_per_query):
        scope_set = {str(s) for s in entry.get("project_scope", [])}
        for ev in hybrid_row.get("evidence_items", []):
            evidence_total += 1
            if str(ev.get("knowledge_scope_id")) not in scope_set:
                leakage_events += 1
            if (
                isinstance(ev.get("source_version"), int)
                and ev["source_version"] >= 1
                and ev.get("source_position")
            ):
                locatable += 1
            if _validate_evidence_item(ev, evidence_item_schema):
                schema_valid += 1
    hard_constraints = {
        "cross_project_leakage_events": leakage_events,
        "schema_validity_rate": (
            schema_valid / evidence_total if evidence_total else 0.0
        ),
        "source_locatability_rate": (
            locatable / evidence_total if evidence_total else 0.0
        ),
        "all_passed": (
            evidence_total > 0
            and leakage_events == 0
            and schema_valid == evidence_total
            and locatable == evidence_total
        ),
    }
    logger.info(
        "Hard constraints measured over %d evidence items: leakage=%d "
        "schema=%.4f locatable=%.4f all_passed=%s",
        evidence_total, leakage_events,
        hard_constraints["schema_validity_rate"],
        hard_constraints["source_locatability_rate"],
        hard_constraints["all_passed"],
    )

    # --- Per-query comparison ---
    per_query_comparison = []
    for i, entry in enumerate(dataset):
        expected_ids = entry.get("expected_evidence_ids", [])
        expected_set = set(expected_ids)

        # Baseline rank
        baseline_retrieved = dense_per_query[i]["retrieved_evidence_ids"]
        baseline_rank = None
        for rank, rid in enumerate(baseline_retrieved, start=1):
            if rid in expected_set:
                baseline_rank = rank
                break

        # Hybrid rank
        hybrid_retrieved = hybrid_per_query[i]["retrieved_evidence_ids"]
        hybrid_rank = None
        for rank, rid in enumerate(hybrid_retrieved, start=1):
            if rid in expected_set:
                hybrid_rank = rank
                break

        # Scores
        baseline_scores = dense_per_query[i].get("scores", [])
        hybrid_scores = hybrid_per_query[i].get("scores", [])
        hybrid_details = hybrid_per_query[i].get("candidate_details", [])

        baseline_dense_score = baseline_scores[baseline_rank - 1] if baseline_rank and baseline_rank <= len(baseline_scores) else None
        # Genuine per-retriever dense score from candidate_details (FR-020/
        # SC-007); the top-level hybrid scores are fused scores, so using
        # them here collapsed dense==fused==rerank in previous reports.
        hybrid_dense_score = None
        for detail in hybrid_details:
            if str(detail.get("chunk_id", "")) in expected_set:
                hybrid_dense_score = detail.get("dense_score")
                break

        # Find expected evidence in hybrid candidate_details for per-retriever scores
        hybrid_sparse_score = None
        hybrid_fused_score = None
        hybrid_rerank_score = None
        for detail in hybrid_details:
            if str(detail.get("chunk_id", "")) in expected_set:
                hybrid_sparse_score = detail.get("sparse_score")
                hybrid_fused_score = detail.get("fused_score")
                hybrid_rerank_score = detail.get("rerank_score")
                break

        rank_improved = (
            hybrid_rank is not None and
            (baseline_rank is None or hybrid_rank < baseline_rank)
        )

        per_query_comparison.append({
            "query_index": i,
            "query": entry["query"],
            "project_scope": entry.get("project_scope", [""])[0] if entry.get("project_scope") else "",
            "expected_evidence_ids": expected_ids,
            "baseline_rank": baseline_rank,
            "hybrid_rank": hybrid_rank,
            "baseline_dense_score": baseline_dense_score,
            "hybrid_dense_score": hybrid_dense_score,
            "hybrid_sparse_score": hybrid_sparse_score,
            "hybrid_fused_score": hybrid_fused_score,
            "hybrid_rerank_score": hybrid_rerank_score,
            "rank_improved": rank_improved,
        })

    # --- Reproducibility check ---
    logger.info("=== Reproducibility Check ===")
    _, hybrid_metrics_2 = await run_single_eval(
        dataset, qdrant_store, embedding_provider, hybrid_collection, top_k,
        mode="hybrid", sparse_encoder=sparse_encoder,
        reranker=reranker,
        rerank_budget=settings.hybrid_retrieval.rerank_budget,
    )
    repro_report = check_reproducibility(hybrid_metrics, hybrid_metrics_2)

    # --- Determine enters_default_path (FR-021 / SC-001 / Constitution X) ---
    # Gate on the ORIGINAL 001 baseline subset (first 11 queries) and require
    # STRICT positive MRR/nDCG deltas plus non-decreasing recall; the previous
    # gate used >= 0 over all queries, which let a zero-gain run enter the
    # default retrieval path.
    n_original = min(11, len(dataset))
    orig_dense_metrics = compute_metrics(dense_per_query[:n_original], top_k)
    orig_hybrid_metrics = compute_metrics(hybrid_per_query[:n_original], top_k)
    def _rel_improvement_pct(new: float, base: float) -> float:
        return round((new - base) / base * 100.0, 4) if base > 0 else 0.0

    original_subset_gate = {
        "num_queries": n_original,
        "baseline_mrr_mean": round(orig_dense_metrics["mrr"]["mean"], 6),
        "hybrid_mrr_mean": round(orig_hybrid_metrics["mrr"]["mean"], 6),
        "baseline_ndcg_mean": round(orig_dense_metrics["ndcg_at_k"]["mean"], 6),
        "hybrid_ndcg_mean": round(orig_hybrid_metrics["ndcg_at_k"]["mean"], 6),
        "baseline_recall_mean": round(orig_dense_metrics["recall_at_k"]["mean"], 6),
        "hybrid_recall_mean": round(orig_hybrid_metrics["recall_at_k"]["mean"], 6),
        "mrr_relative_improvement_pct": _rel_improvement_pct(
            orig_hybrid_metrics["mrr"]["mean"], orig_dense_metrics["mrr"]["mean"]
        ),
        "ndcg_relative_improvement_pct": _rel_improvement_pct(
            orig_hybrid_metrics["ndcg_at_k"]["mean"],
            orig_dense_metrics["ndcg_at_k"]["mean"],
        ),
        "mrr_positive_delta": (
            orig_hybrid_metrics["mrr"]["mean"] > orig_dense_metrics["mrr"]["mean"]
        ),
        "ndcg_positive_delta": (
            orig_hybrid_metrics["ndcg_at_k"]["mean"]
            > orig_dense_metrics["ndcg_at_k"]["mean"]
        ),
        "recall_non_decreasing": (
            orig_hybrid_metrics["recall_at_k"]["mean"]
            >= orig_dense_metrics["recall_at_k"]["mean"]
        ),
    }
    # Constitution X / FR-021 / SC-001 (relative criterion, research.md §0.2
    # revised 2026-09-04): measurable benefit = STRICT positive MRR/nDCG
    # deltas on the original-11 subset with non-decreasing recall and all
    # measured hard constraints passing. Absolute metric levels are
    # environment-dependent, recorded for reference only, and do not gate
    # (the previous absolute thresholds 0.95/0.96 were old-environment
    # operationalizations and are superseded).
    enters_default_path = (
        original_subset_gate["mrr_positive_delta"]
        and original_subset_gate["ndcg_positive_delta"]
        and original_subset_gate["recall_non_decreasing"]
        and hard_constraints["all_passed"]
    )

    # --- Build report ---
    def _metric_block(m: dict) -> dict:
        return {"mean": round(m["mean"], 4), "min": round(m["min"], 4), "max": round(m["max"], 4)}

    def _latency_block(m: dict) -> dict:
        return {
            "p50": round(m["p50"], 2),
            "p95": round(m["p95"], 2),
            "mean": round(m["mean"], 2),
            "min": round(m["min"], 2),
            "max": round(m["max"], 2),
        }

    report = {
        "report_type": "hybrid_retrieval_comparison",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "embedding_model": settings.embedding_model,
            "reranker_model": settings.hybrid_retrieval.reranker_model,
            "dense_collection": dense_collection,
            "hybrid_collection": hybrid_collection,
            "fusion_algorithm": settings.hybrid_retrieval.fusion_algorithm,
            "rrf_k": settings.hybrid_retrieval.rrf_k,
            "rerank_budget": settings.hybrid_retrieval.rerank_budget,
            "dataset_path": str(ds_path.resolve()),
            "num_queries": len(dataset),
        },
        "baseline_metrics": {
            "recall_at_k": _metric_block(dense_metrics["recall_at_k"]),
            "mrr": _metric_block(dense_metrics["mrr"]),
            "ndcg_at_k": _metric_block(dense_metrics["ndcg_at_k"]),
            "latency_ms": _latency_block(dense_metrics["latency_ms"]),
        },
        "hybrid_metrics": {
            "recall_at_k": _metric_block(hybrid_metrics["recall_at_k"]),
            "mrr": _metric_block(hybrid_metrics["mrr"]),
            "ndcg_at_k": _metric_block(hybrid_metrics["ndcg_at_k"]),
            "latency_ms": _latency_block(hybrid_metrics["latency_ms"]),
        },
        "deltas": {k: round(v, 6) for k, v in deltas.items()},
        "hard_constraints": hard_constraints,
        "per_query_comparison": per_query_comparison,
        "reproducibility": {
            "non_latency_reproducible": (
                all(c.get("passed", False) for c in repro_report.get("checks", [])
                    if "latency" not in c.get("metric", ""))
                if repro_report.get("checks") else True
            ),
            "tolerance": repro_report.get("tolerance", 0.01),
            "checks": repro_report.get("checks", []),
        },
        "enters_default_path": enters_default_path,
        "original_subset_gate": original_subset_gate,
    }

    # Write report
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("Comparison report written to %s", out_path)
    logger.info("enters_default_path: %s", enters_default_path)
    logger.info("  MRR: %.4f -> %.4f (delta %.4f)", dense_metrics["mrr"]["mean"], hybrid_metrics["mrr"]["mean"], deltas["mrr_mean_delta"])
    logger.info("  nDCG: %.4f -> %.4f (delta %.4f)", dense_metrics["ndcg_at_k"]["mean"], hybrid_metrics["ndcg_at_k"]["mean"], deltas["ndcg_mean_delta"])
    logger.info(
        "  Original-11 gate: MRR %.4f -> %.4f | nDCG %.4f -> %.4f | recall %.4f -> %.4f",
        original_subset_gate["baseline_mrr_mean"], original_subset_gate["hybrid_mrr_mean"],
        original_subset_gate["baseline_ndcg_mean"], original_subset_gate["hybrid_ndcg_mean"],
        original_subset_gate["baseline_recall_mean"], original_subset_gate["hybrid_recall_mean"],
    )

    # --- Format expansion report (T039 / FR-026, scoped by T055) ---
    # A limited run (002 fixed scope) writes its side-effect format report
    # to a distinct 002-scoped path; only an unlimited (full dataset) run
    # regenerates the declared 003 artifact.
    _format_report_path = _format_report_target(format_report_output, limit)
    _is_limited = limit is not None and limit > 0
    generate_format_expansion_report(
        dataset=dataset,
        dense_per_query=dense_per_query,
        hybrid_per_query=hybrid_per_query,
        dense_metrics=dense_metrics,
        hybrid_metrics=hybrid_metrics,
        top_k=top_k,
        config=report["config"],
        output_path=str(_format_report_path),
        skip_write_when_limited=_is_limited and not format_report_output,
    )

    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Dense vs Hybrid comparison evaluation (FR-019/FR-024/SC-006/SC-007).",
    )
    parser.add_argument("--dataset", "-d", required=True, type=str)
    parser.add_argument("--output", "-o", required=True, type=str)
    parser.add_argument("--top-k", "-k", type=int, default=5)
    parser.add_argument(
        "--limit", "-n", type=int, default=None,
        help="Only evaluate the first N dataset entries (002 fixed scope: 18).",
    )
    parser.add_argument("--qdrant-url", type=str, default=None)
    parser.add_argument(
        "--format-report", type=str, default=None,
        help="Output path for the per-format expansion report "
             "(default: eval/format_expansion_report.json).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", default=False)
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return await run_comparison(
        dataset_path=args.dataset,
        output_path=args.output,
        top_k=args.top_k,
        qdrant_url=args.qdrant_url,
        format_report_output=args.format_report,
        limit=args.limit,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
