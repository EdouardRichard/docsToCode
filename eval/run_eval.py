#!/usr/bin/env python3
"""Evaluation runner for Dense retrieval baseline (FR-024 / SC-009).

Loads an eval dataset, runs each query against the retrieval service, and
computes standard IR metrics: Recall@K, MRR, nDCG@K, and latency percentiles.

SC-009 reproducibility check: runs the full evaluation twice and verifies that
all numeric metrics match within 1% relative tolerance.

Usage:
    python eval/run_eval.py --dataset eval/eval_dataset.json --output eval/baseline_report.json
    python eval/run_eval.py --dataset eval/eval_dataset.json --output eval/baseline_report.json --top-k 10
    python eval/run_eval.py --dataset eval/eval_dataset.json --output eval/baseline_report.json --no-reproducibility-check
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure backend source is importable when running from repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.providers.base import EmbeddingProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stub embedding provider for eval (uses random vectors as placeholder)
# ---------------------------------------------------------------------------

class _EvalEmbeddingProvider(EmbeddingProvider):
    """Minimal embedding provider for evaluation.

    In production this would load the real model.  For initial baseline
    evaluation we attempt to import and use LocalCPUEmbeddingProvider if
    available; otherwise fall back to a deterministic hash-based stub so
    the script remains runnable without GPU/model dependencies.
    """

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or get_settings().embedding_model
        self._dimension = 1024  # bge-m3 default
        self._real_provider: EmbeddingProvider | None = None

        # Try to load real provider
        try:
            from rag_mcp.providers.local_cpu import LocalCPUEmbeddingProvider
            self._real_provider = LocalCPUEmbeddingProvider(self._model_name)
            self._dimension = self._real_provider.get_dimension()
            logger.info("Loaded real embedding provider: %s (dim=%d)", self._model_name, self._dimension)
        except (ImportError, Exception) as exc:
            logger.warning(
                "Could not load real embedding provider (%s). "
                "Using deterministic hash-based stub. Results will NOT reflect real retrieval quality.",
                exc,
            )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self._real_provider:
            return await self._real_provider.embed_texts(texts)
        return [self._hash_vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        if self._real_provider:
            return await self._real_provider.embed_query(text)
        return self._hash_vector(text)

    def get_dimension(self) -> int:
        if self._real_provider:
            return self._real_provider.get_dimension()
        return self._dimension

    def _hash_vector(self, text: str) -> list[float]:
        """Deterministic pseudo-vector from text hash. Not semantically meaningful."""
        import hashlib
        digest = hashlib.sha256(text.encode()).digest()
        # Expand 32 bytes to dimension floats
        vec: list[float] = []
        for i in range(self._dimension):
            byte_idx = i % len(digest)
            val = (digest[byte_idx] + i * 7) % 256
            vec.append(val / 255.0)
        # Normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


# ---------------------------------------------------------------------------
# Retrieval function (direct service call)
# ---------------------------------------------------------------------------

async def search_knowledge(
    query: str,
    project_scope_ids: list[int],
    qdrant_store: QdrantStore,
    embedding_provider: EmbeddingProvider,
    collection_name: str,
    top_k: int = 5,
    mode: str = "dense",
    sparse_encoder: Any = None,
) -> dict[str, Any]:
    """Execute a retrieval query directly via QdrantStore.

    Returns a result dict with evidence_ids, scores, and timing info.
    This bypasses MCP protocol for eval simplicity.
    """
    start_time = time.perf_counter()

    try:
        # Embed the query
        query_vector = await embedding_provider.embed_query(query)

        if mode == "hybrid" and sparse_encoder is not None:
            # Hybrid: Dense + Sparse parallel recall + RRF fusion
            sparse_query = sparse_encoder.encode(query)
            dense_results, sparse_results = qdrant_store.query_hybrid(
                collection=collection_name,
                dense_vector=query_vector,
                sparse_vector=sparse_query,
                scope_ids=project_scope_ids,
                limit=top_k,
            )
            # RRF fusion
            from rag_mcp.fusion.rrf import rrf_fuse
            fused = rrf_fuse(dense_results, sparse_results, k=60)
            # Build result map
            result_map = {}
            for r in dense_results + sparse_results:
                payload = r.get("payload") or {}
                cid = str(payload.get("chunk_id", r.get("id", "")))
                if cid and cid not in result_map:
                    result_map[cid] = r
            results = [
                {"id": result_map.get(c.chunk_id, {}).get("id"),
                 "score": c.fused_score,
                 "payload": result_map.get(c.chunk_id, {}).get("payload", {})}
                for c in fused
            ]
            # Per-candidate detail scores (FR-020/SC-007)
            candidate_details = [
                {
                    "chunk_id": c.chunk_id,
                    "dense_score": c.dense_score,
                    "sparse_score": c.sparse_score,
                    "fused_score": c.fused_score,
                    "rerank_score": c.fused_score,  # RRF score as rerank fallback
                }
                for c in fused
            ]
        else:
            # Dense-only search
            results = qdrant_store.search(
                collection=collection_name,
                vector=query_vector,
                scope_ids=project_scope_ids,
                limit=top_k,
            )

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        evidence_ids = [str(r["id"]) for r in results]
        scores = [r["score"] for r in results]

        result = {
            "evidence_ids": evidence_ids,
            "scores": scores,
            "latency_ms": elapsed_ms,
            "status": "ok",
        }
        if mode == "hybrid" and sparse_encoder is not None:
            result["candidate_details"] = candidate_details
        return result

    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.error("Search failed for query '%s': %s", query[:80], exc)
        return {
            "evidence_ids": [],
            "scores": [],
            "latency_ms": elapsed_ms,
            "status": "error",
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_recall_at_k(
    expected_ids: list[str],
    retrieved_ids: list[str],
    k: int,
) -> float:
    """Recall@K: fraction of expected evidence found in top-K results."""
    if not expected_ids:
        return 1.0  # No ground truth → perfect recall by convention
    retrieved_set = set(retrieved_ids[:k])
    hits = sum(1 for eid in expected_ids if eid in retrieved_set)
    return hits / len(expected_ids)


def compute_mrr(expected_ids: list[str], retrieved_ids: list[str]) -> float:
    """Mean Reciprocal Rank: 1/rank of first matching evidence."""
    expected_set = set(expected_ids)
    for rank, rid in enumerate(retrieved_ids, start=1):
        if rid in expected_set:
            return 1.0 / rank
    return 0.0


def compute_ndcg_at_k(
    expected_ids: list[str],
    retrieved_ids: list[str],
    k: int,
) -> float:
    """Normalized Discounted Cumulative Gain @ K.

    Binary relevance: 1 if retrieved item is in expected set, 0 otherwise.
    """
    if not expected_ids:
        return 1.0

    expected_set = set(expected_ids)

    # DCG
    dcg = 0.0
    for i, rid in enumerate(retrieved_ids[:k]):
        rel = 1.0 if rid in expected_set else 0.0
        dcg += rel / math.log2(i + 2)  # i+2 because rank starts at 1

    # Ideal DCG: all relevant items ranked first
    ideal_hits = min(len(expected_ids), k)
    idcg = 0.0
    for i in range(ideal_hits):
        idcg += 1.0 / math.log2(i + 2)

    if idcg == 0:
        return 0.0

    return dcg / idcg


def compute_percentile(values: list[float], percentile: float) -> float:
    """Compute a percentile value from a sorted list."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = (percentile / 100.0) * (len(sorted_vals) - 1)
    lower = int(math.floor(idx))
    upper = int(math.ceil(idx))
    if lower == upper:
        return sorted_vals[lower]
    frac = idx - lower
    return sorted_vals[lower] * (1 - frac) + sorted_vals[upper] * frac


def compute_metrics(
    per_query_results: list[dict[str, Any]],
    top_k: int,
) -> dict[str, Any]:
    """Aggregate metrics across all queries."""
    recalls: list[float] = []
    mrrs: list[float] = []
    ndcgs: list[float] = []
    latencies: list[float] = []

    for result in per_query_results:
        expected = result["expected_evidence_ids"]
        retrieved = result["retrieved_evidence_ids"]
        latency = result["latency_ms"]

        recalls.append(compute_recall_at_k(expected, retrieved, top_k))
        mrrs.append(compute_mrr(expected, retrieved))
        ndcgs.append(compute_ndcg_at_k(expected, retrieved, top_k))
        latencies.append(latency)

    n = len(per_query_results)

    return {
        "num_queries": n,
        "top_k": top_k,
        "recall_at_k": {
            "mean": sum(recalls) / n if n else 0.0,
            "min": min(recalls) if recalls else 0.0,
            "max": max(recalls) if recalls else 0.0,
        },
        "mrr": {
            "mean": sum(mrrs) / n if n else 0.0,
            "min": min(mrrs) if mrrs else 0.0,
            "max": max(mrrs) if mrrs else 0.0,
        },
        "ndcg_at_k": {
            "mean": sum(ndcgs) / n if n else 0.0,
            "min": min(ndcgs) if ndcgs else 0.0,
            "max": max(ndcgs) if ndcgs else 0.0,
        },
        "latency_ms": {
            "p50": compute_percentile(latencies, 50),
            "p95": compute_percentile(latencies, 95),
            "mean": sum(latencies) / n if n else 0.0,
            "min": min(latencies) if latencies else 0.0,
            "max": max(latencies) if latencies else 0.0,
        },
    }


# ---------------------------------------------------------------------------
# Reproducibility check (SC-009)
# ---------------------------------------------------------------------------

def check_reproducibility(
    metrics_a: dict[str, Any],
    metrics_b: dict[str, Any],
    tolerance: float = 0.01,
) -> dict[str, Any]:
    """Verify two metric sets match within relative tolerance.

    Returns a report with pass/fail status and per-metric deltas.
    """
    checks: list[dict[str, Any]] = []
    all_passed = True

    # Compare scalar metrics
    metric_paths = [
        ("recall_at_k.mean", metrics_a["recall_at_k"]["mean"], metrics_b["recall_at_k"]["mean"]),
        ("mrr.mean", metrics_a["mrr"]["mean"], metrics_b["mrr"]["mean"]),
        ("ndcg_at_k.mean", metrics_a["ndcg_at_k"]["mean"], metrics_b["ndcg_at_k"]["mean"]),
        ("latency_ms.p50", metrics_a["latency_ms"]["p50"], metrics_b["latency_ms"]["p50"]),
        ("latency_ms.p95", metrics_a["latency_ms"]["p95"], metrics_b["latency_ms"]["p95"]),
    ]

    for name, val_a, val_b in metric_paths:
        if val_a == 0 and val_b == 0:
            delta = 0.0
            passed = True
        elif val_a == 0 or val_b == 0:
            delta = abs(val_a - val_b)
            passed = delta <= tolerance
        else:
            delta = abs(val_a - val_b) / max(abs(val_a), abs(val_b))
            passed = delta <= tolerance

        if not passed:
            all_passed = False

        checks.append({
            "metric": name,
            "run_1": round(val_a, 6),
            "run_2": round(val_b, 6),
            "relative_delta": round(delta, 6),
            "tolerance": tolerance,
            "passed": passed,
        })

    return {
        "reproducible": all_passed,
        "tolerance": tolerance,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Evaluation run
# ---------------------------------------------------------------------------

async def run_single_eval(
    dataset: list[dict[str, Any]],
    qdrant_store: QdrantStore,
    embedding_provider: EmbeddingProvider,
    collection_name: str,
    top_k: int,
    mode: str = "dense",
    sparse_encoder: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run one complete evaluation pass.

    Returns:
        Tuple of (per_query_results, aggregated_metrics).
    """
    per_query_results: list[dict[str, Any]] = []

    for i, entry in enumerate(dataset):
        query = entry["query"]
        project_scope = entry.get("project_scope", [])
        expected_ids = entry.get("expected_evidence_ids", [])

        # Convert scope IDs to integers for Qdrant filtering
        scope_ids_int = [int(s) for s in project_scope]

        result = await search_knowledge(
            query=query,
            project_scope_ids=scope_ids_int,
            qdrant_store=qdrant_store,
            embedding_provider=embedding_provider,
            collection_name=collection_name,
            top_k=top_k,
            mode=mode,
            sparse_encoder=sparse_encoder,
        )

        per_query_result = {
            "query_index": i,
            "query": query,
            "project_scope": project_scope,
            "expected_evidence_ids": expected_ids,
            "retrieved_evidence_ids": result["evidence_ids"],
            "scores": result["scores"],
            "latency_ms": result["latency_ms"],
            "status": result["status"],
            "candidate_details": result.get("candidate_details", []),
        }
        per_query_results.append(per_query_result)

        if (i + 1) % 10 == 0 or i == len(dataset) - 1:
            logger.info("  Processed %d/%d queries", i + 1, len(dataset))

    metrics = compute_metrics(per_query_results, top_k)
    return per_query_results, metrics


async def run_evaluation(
    dataset_path: str,
    output_path: str,
    top_k: int = 5,
    skip_reproducibility: bool = False,
    db_url: str | None = None,
    qdrant_url: str | None = None,
    mode: str = "dense",
) -> int:
    """Main evaluation orchestration.

    Args:
        dataset_path: Path to eval_dataset.json.
        output_path: Path for baseline_report.json output.
        top_k: Number of results to retrieve per query.
        skip_reproducibility: If True, skip the second run.
        db_url: Optional database URL override.
        qdrant_url: Optional Qdrant URL override.

    Returns:
        Exit code (0 = success).
    """
    settings = get_settings()

    # Load dataset
    ds_path = Path(dataset_path)
    if not ds_path.exists():
        logger.error("Dataset file not found: %s", dataset_path)
        return 1

    with open(ds_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    if not dataset:
        logger.error("Dataset is empty. Nothing to evaluate.")
        return 1

    logger.info("Loaded %d queries from %s", len(dataset), dataset_path)

    # Initialize components
    embedding_provider = _EvalEmbeddingProvider(settings.embedding_model)
    qdrant_store = QdrantStore(url=qdrant_url or settings.qdrant_url)

    # Determine collection name from index_version convention
    from rag_mcp.services.ingestion_service import _derive_index_version
    index_version = _derive_index_version(settings.embedding_model)
    if mode == "hybrid":
        collection_name = f"chunks_hybrid_{index_version}"
    else:
        collection_name = f"chunks_dense_{index_version}"

    logger.info("Mode: %s | Collection: %s | Top-K: %d", mode, collection_name, top_k)

    # Check collection exists
    if not qdrant_store.collection_exists(collection_name):
        logger.error(
            "Qdrant collection '%s' does not exist. "
            "Ensure knowledge has been ingested before running eval.",
            collection_name,
        )
        return 1

    # Build sparse encoder for hybrid mode
    sparse_encoder = None
    if mode == "hybrid":
        from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder
        from sqlalchemy import select as sa_select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker
        from rag_mcp.models.chunk import Chunk
        from rag_mcp.models.knowledge_version import KnowledgeVersion

        engine = create_async_engine(settings.database_url)
        async with engine.begin() as conn:
            pass
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            result = await session.execute(
                sa_select(Chunk.content_text)
                .join(KnowledgeVersion, Chunk.version_id == KnowledgeVersion.version_id)
                .where(KnowledgeVersion.status == "published")
                .order_by(Chunk.chunk_id)
            )
            texts = [row[0] for row in result.all()]
        await engine.dispose()
        if texts:
            sparse_encoder = BM25SparseEncoder()
            sparse_encoder.fit(texts)
            logger.info("Sparse encoder fitted on %d chunk texts", len(texts))
        else:
            logger.warning("No chunk texts found for sparse encoder, falling back to dense")

    # --- Run 1 ---
    logger.info("=== Evaluation Run 1 ===")
    run1_start = time.time()
    per_query_1, metrics_1 = await run_single_eval(
        dataset, qdrant_store, embedding_provider, collection_name, top_k,
        mode=mode, sparse_encoder=sparse_encoder,
    )
    run1_elapsed = time.time() - run1_start
    logger.info("Run 1 completed in %.2fs", run1_elapsed)

    # --- Run 2 (reproducibility) ---
    reproducibility_report: dict[str, Any] | None = None

    if not skip_reproducibility:
        logger.info("=== Evaluation Run 2 (Reproducibility Check) ===")
        run2_start = time.time()
        _, metrics_2 = await run_single_eval(
            dataset, qdrant_store, embedding_provider, collection_name, top_k,
            mode=mode, sparse_encoder=sparse_encoder,
        )
        run2_elapsed = time.time() - run2_start
        logger.info("Run 2 completed in %.2fs", run2_elapsed)

        reproducibility_report = check_reproducibility(metrics_1, metrics_2)

        if reproducibility_report["reproducible"]:
            logger.info("✓ Reproducibility check PASSED (tolerance: 1%%)")
        else:
            logger.warning("✗ Reproducibility check FAILED:")
            for check in reproducibility_report["checks"]:
                if not check["passed"]:
                    logger.warning(
                        "  %s: run1=%.6f run2=%.6f delta=%.6f",
                        check["metric"], check["run_1"], check["run_2"],
                        check["relative_delta"],
                    )
    else:
        logger.info("Skipping reproducibility check (--no-reproducibility-check)")

    # --- Build report ---
    from datetime import datetime, timezone

    report: dict[str, Any] = {
        "report_type": "dense_retrieval_baseline",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "embedding_model": settings.embedding_model,
            "qdrant_collection": collection_name,
            "retrieval_mode": mode,
            "top_k": top_k,
            "dataset_path": str(ds_path.resolve()),
            "num_queries": len(dataset),
        },
        "metrics": metrics_1,
        "per_query_results": per_query_1,
    }

    if reproducibility_report is not None:
        report["reproducibility"] = reproducibility_report

    # Write report
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info("Baseline report written to %s", out_path)

    # Summary
    m = metrics_1
    logger.info("=== Baseline Summary ===")
    logger.info("  Queries:     %d", m["num_queries"])
    logger.info("  Recall@%d:   %.4f", top_k, m["recall_at_k"]["mean"])
    logger.info("  MRR:         %.4f", m["mrr"]["mean"])
    logger.info("  nDCG@%d:     %.4f", top_k, m["ndcg_at_k"]["mean"])
    logger.info("  Latency P50: %.2f ms", m["latency_ms"]["p50"])
    logger.info("  Latency P95: %.2f ms", m["latency_ms"]["p95"])

    if reproducibility_report:
        status = "PASSED" if reproducibility_report["reproducible"] else "FAILED"
        logger.info("  Reproducibility: %s", status)

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Dense retrieval evaluation and produce baseline report (FR-024 / SC-009).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python eval/run_eval.py --dataset eval/eval_dataset.json --output eval/baseline_report.json\n"
            "  python eval/run_eval.py --dataset eval/eval_dataset.json --output eval/baseline_report.json --top-k 10\n"
            "  python eval/run_eval.py --dataset eval/eval_dataset.json --output eval/baseline_report.json --no-reproducibility-check\n"
        ),
    )
    parser.add_argument(
        "--dataset", "-d",
        required=True,
        type=str,
        help="Path to eval dataset JSON file.",
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        type=str,
        help="Output path for the baseline report JSON.",
    )
    parser.add_argument(
        "--top-k", "-k",
        type=int,
        default=5,
        help="Number of results to retrieve per query (default: 5, max: 20).",
    )
    parser.add_argument(
        "--mode", "-m",
        type=str,
        choices=["dense", "hybrid"],
        default="dense",
        help="Retrieval mode: dense (001 baseline) or hybrid (002 Dense+Sparse+RRF).",
    )
    parser.add_argument(
        "--no-reproducibility-check",
        action="store_true",
        default=False,
        help="Skip the second evaluation run (SC-009 reproducibility check).",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Database URL override (not used in direct mode but reserved).",
    )
    parser.add_argument(
        "--qdrant-url",
        type=str,
        default=None,
        help="Qdrant URL override.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable verbose logging.",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.top_k < 1 or args.top_k > 20:
        logger.error("--top-k must be between 1 and 20 (got %d)", args.top_k)
        return 1

    return await run_evaluation(
        dataset_path=args.dataset,
        output_path=args.output,
        top_k=args.top_k,
        skip_reproducibility=args.no_reproducibility_check,
        db_url=args.db_url,
        qdrant_url=args.qdrant_url,
        mode=args.mode,
    )


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
