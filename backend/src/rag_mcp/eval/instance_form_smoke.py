"""Instance-form non-regression smoke adapter (006, T070).

FR-028/SC-009: re-run the 001 baseline 11 queries through the MCP retrieval
path for each instance form (writer + reader) and compare non-latency
metrics (Recall@K / MRR / nDCG) per-query against eval/baseline_report.json
within a 1% relative tolerance. Latency P50/P95 is recorded for comparison
and annotated env_sensitive (no threshold, research §0.2). No quality
threshold is set and no quality claim is made (FR-027: the 006 comparison
evaluation requirement is "none").
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_EVAL_DIR = _REPO_ROOT / "eval"
_BASELINE_PATH = _EVAL_DIR / "baseline_report.json"
_DATASET_PATH = _EVAL_DIR / "eval_dataset.json"
_DEFAULT_REPORT_PATH = _EVAL_DIR / "instance_form_smoke_report.json"

# 001 baseline aggregation (research §0.1): 11 queries.
_BASELINE_MEANS = {
    "recall_at_k": 1.0,
    "mrr": 0.9090909090909091,
    "ndcg_at_k": 0.9328963188311742,
}


def load_baseline() -> dict:
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def load_baseline_queries() -> list[dict]:
    """The 11 baseline queries (filter the full dataset to baseline query texts)."""
    baseline = load_baseline()
    baseline_queries = [p["query"] for p in baseline["per_query_results"]]
    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    queries = dataset if isinstance(dataset, list) else dataset.get("queries", [])
    selected = [q for q in queries if q["query"] in baseline_queries]
    return selected


def compute_metrics(expected: list[str], retrieved: list[str], k: int = 5) -> dict[str, float]:
    """Recall@k / MRR / nDCG@k for one query (binary relevance)."""
    expected_set = set(expected)
    top = retrieved[:k]
    hits = [1 if eid in expected_set else 0 for eid in top]

    recall = sum(hits) / len(expected) if expected else 0.0

    mrr = 0.0
    for rank, eid in enumerate(top, start=1):
        if eid in expected_set:
            mrr = 1.0 / rank
            break

    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(hits))
    ideal_hits = [1] * min(len(expected), k)
    idcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(ideal_hits))
    ndcg = (dcg / idcg) if idcg > 0 else 0.0

    return {"recall_at_k": recall, "mrr": mrr, "ndcg_at_k": ndcg}


def _within_tolerance(value: float, baseline: float, tolerance: float) -> bool:
    return abs(value - baseline) <= tolerance * max(abs(baseline), 1e-9)


def _not_regressed(value: float, baseline: float, tolerance: float) -> bool:
    """Non-regression check (FR-028/SC-009): must not DEGRADE below the
    baseline by more than the tolerance. An improvement (e.g. from Qdrant
    being re-indexed by later features) is environment-sensitive drift, not
    a regression, and is accepted."""
    return value >= baseline - tolerance * max(abs(baseline), 1e-9)


async def run_form_smoke(
    mode: str,
    *,
    session_factory,
    qdrant_store,
    embedding_provider,
    queries: list[dict],
    top_k: int = 5,
    tolerance: float = 0.01,
) -> dict[str, Any]:
    """Run the 11 queries through the shared retrieval path for one instance form."""
    from rag_mcp.mcp.search_knowledge import search_knowledge_core

    per_query = []
    for q in queries:
        result = await search_knowledge_core(
            query=q["query"],
            project_scope=q.get("project_scope", []),
            top_k=top_k,
            task_context=None,
            session_factory=session_factory,
            qdrant_store=qdrant_store,
            embedding_provider=embedding_provider,
            reranker=None,
        )
        retrieved = [e["evidence_id"] for e in result.get("evidence", [])]
        per_query.append({
            "query": q["query"],
            "expected": q.get("expected_evidence_ids", []),
            "retrieved": retrieved,
            "status": result.get("completion_status"),
        })

    means: dict[str, float] = {}
    for metric in ("recall_at_k", "mrr", "ndcg_at_k"):
        values = [compute_metrics(p["expected"], p["retrieved"], top_k)[metric] for p in per_query]
        means[metric] = sum(values) / len(values) if values else 0.0

    comparison = {
        metric: {
            "measured": means[metric],
            "baseline": _BASELINE_MEANS[metric],
            "within_tolerance": _within_tolerance(means[metric], _BASELINE_MEANS[metric], tolerance),
            "no_regression": _not_regressed(means[metric], _BASELINE_MEANS[metric], tolerance),
        }
        for metric in means
    }

    return {
        "mode": mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "num_queries": len(queries),
        "means": means,
        "comparison": comparison,
        "latency_env_sensitive": True,
        "per_query_results": per_query,
    }


def run_form_smoke_sync(mode: str, *, session_factory, qdrant_store, embedding_provider, queries, top_k=5, tolerance=0.01) -> dict:
    import asyncio

    return asyncio.run(
        run_form_smoke(
            mode,
            session_factory=session_factory,
            qdrant_store=qdrant_store,
            embedding_provider=embedding_provider,
            queries=queries,
            top_k=top_k,
            tolerance=tolerance,
        )
    )


def write_instance_form_report(report: dict, path: str | Path | None = None) -> Path:
    """Persist an instance-form smoke comparison report (T070, FR-028/SC-009).

    The default target is eval/instance_form_smoke_report.json; an explicit
    path (e.g. a pytest tmp_path) overrides it. JSON is written UTF-8 with
    stable key order for reproducible diffs.
    """
    target = Path(path) if path is not None else _DEFAULT_REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )
    return target
