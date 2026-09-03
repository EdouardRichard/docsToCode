#!/usr/bin/env python3
"""Agentic comparison evaluation runner (005, T061).

Runs the deterministic default path (re-run in the same session, FR-030) and
the Agent orchestration path over the combined evaluation set (001 11-query
baseline + 002/004 extended set + 005 agentic batch), computes
Recall@K / MRR / nDCG@K / P50-P95 latency / cost, records per-query
dual-path ranks with the Agent judgment (sub-problems/signals/directions/
gaps/supplementary rounds/orchestration decision), sub-path timings and
ledger refs (FR-028/SC-009), applies the three-gate pass decision
(SC-001/SC-002/SC-015) plus hard constraints, and persists
eval/agentic_comparison_report.json with enters_default_path (FR-029).

Usage:
    python eval/run_agentic_comparison.py \
        --dataset eval/eval_dataset.json \
        --agentic-dataset eval/agentic_eval_dataset.json \
        --output eval/agentic_comparison_report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _REPO_ROOT / "backend" / "src"
for p in (_BACKEND_SRC, str(_REPO_ROOT / "eval"), str(_REPO_ROOT / "backend")):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from sqlalchemy import select as sa_select

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.models.chunk import Chunk
from rag_mcp.eval.agentic_comparison import AgenticComparisonRunner
from run_eval import (  # noqa: E402
    _EvalEmbeddingProvider,
    check_reproducibility,
    compute_metrics,
    compute_mrr,
    compute_ndcg_at_k,
    compute_recall_at_k,
)
from run_graph_comparison import ensure_graph_corpus  # noqa: E402

logger = logging.getLogger(__name__)

SC001_THRESHOLD_PCT = 3.0   # relative improvement percent (SC-001)
SC_TOLERANCE = 0.01         # absolute non-inferiority tolerance (research §10)
REPEAT_TOLERANCE = 0.01     # reproducibility relative tolerance (SC-008)
_001_QUERY_COUNT = 11


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


def load_combined_dataset(base_path: str, agentic_path: str) -> list[dict[str, Any]]:
    """Combine the 001/002/004 fixed set with the 005 agentic batch.

    Original entries stay in order (per-query comparability, FR-021); the
    005 batch is appended and tagged.
    """
    with open(base_path, "r", encoding="utf-8") as f:
        base = json.load(f)
    with open(agentic_path, "r", encoding="utf-8") as f:
        agentic = json.load(f)
    combined = [dict(e) for e in base]
    for e in agentic:
        entry = dict(e)
        entry.setdefault("_source", "005")
        combined.append(entry)
    return combined


def _is_beneficiary(entry: dict[str, Any]) -> bool:
    """Agent-beneficiary entries: 004 structural + 005 batch categories."""
    if entry.get("is_structural_benefit"):
        return True
    return entry.get("category") in ("multi_hop", "gap", "conflict")


# ---------------------------------------------------------------------------
# Path execution (same-session fairness, FR-030)
# ---------------------------------------------------------------------------


async def run_fair_paths(
    dataset: list[dict[str, Any]],
    baseline_fn: Callable,
    agentic_fn: Callable,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rerun the deterministic baseline FIRST, then the agentic path (FR-030)."""
    baseline_results = await baseline_fn(dataset)
    agentic_results = await agentic_fn(dataset)
    return baseline_results, agentic_results


async def run_deterministic_pass(
    dataset: list[dict[str, Any]],
    session_factory,
    qdrant_store: QdrantStore,
    embedding_provider,
    reranker,
    top_k: int,
) -> list[dict[str, Any]]:
    """Deterministic default path: RetrievalService.search (switches OFF)."""
    from rag_mcp.services.retrieval_service import RetrievalService

    results: list[dict[str, Any]] = []
    for i, entry in enumerate(dataset):
        start = time.perf_counter()
        async with session_factory() as session:
            service = RetrievalService(
                session=session,
                qdrant_store=qdrant_store,
                embedding_provider=embedding_provider,
                reranker=reranker,
            )
            resp = await service.search(
                query=entry["query"],
                project_scopes=list(entry.get("project_scope", [])),
                top_k=top_k,
                task_context=None,
            )
            await session.commit()
        latency = (time.perf_counter() - start) * 1000.0
        evidence = resp.get("evidence", [])
        results.append({
            "expected_evidence_ids": entry.get("expected_evidence_ids", []),
            "retrieved_evidence_ids": [e["evidence_id"] for e in evidence],
            "scopes": [e.get("knowledge_scope_id", "") for e in evidence],
            "latency_ms": latency,
            "status": resp.get("completion_status", "failed"),
            "request_id": resp.get("request_id", ""),
            "response": resp,
        })
        if (i + 1) % 10 == 0 or i == len(dataset) - 1:
            logger.info("  deterministic pass %d/%d", i + 1, len(dataset))
    return results


async def run_agentic_pass(
    dataset: list[dict[str, Any]],
    session_factory,
    qdrant_store: QdrantStore,
    embedding_provider,
    reranker,
    top_k: int,
) -> list[dict[str, Any]]:
    """Agent orchestration path: run_agentic_search (real state machine).

    Production semantics (T057): when the agentic path cannot produce a
    response within the guardrails (e.g. the 30s total timeout), the request
    degrades to the deterministic path instead of failing (SC-011/SC-012).
    Such queries are tagged degraded_to_deterministic.
    """
    from rag_mcp.orchestration.entry import AgenticPathUnavailable, run_agentic_search
    from rag_mcp.services.retrieval_service import RetrievalService

    results: list[dict[str, Any]] = []
    for i, entry in enumerate(dataset):
        start = time.perf_counter()
        degraded = False
        try:
            resp, record = await run_agentic_search(
                query=entry["query"],
                project_scopes=list(entry.get("project_scope", [])),
                top_k=top_k,
                task_context=None,
                session_factory=session_factory,
                qdrant_store=qdrant_store,
                embedding_provider=embedding_provider,
                reranker=reranker,
                return_record=True,
            )
        except AgenticPathUnavailable as exc:
            logger.warning(
                "Agentic path unavailable for query %d (%s); deterministic degradation",
                i, exc,
            )
            degraded = True
            record = {}
            async with session_factory() as session:
                service = RetrievalService(
                    session=session,
                    qdrant_store=qdrant_store,
                    embedding_provider=embedding_provider,
                    reranker=reranker,
                )
                resp = await service.search(
                    query=entry["query"],
                    project_scopes=list(entry.get("project_scope", [])),
                    top_k=top_k,
                    task_context=None,
                )
                await session.commit()
        latency = (time.perf_counter() - start) * 1000.0
        evidence = resp.get("evidence", [])
        results.append({
            "expected_evidence_ids": entry.get("expected_evidence_ids", []),
            "retrieved_evidence_ids": [e["evidence_id"] for e in evidence],
            "scopes": [e.get("knowledge_scope_id", "") for e in evidence],
            "latency_ms": latency,
            "status": resp.get("completion_status", "failed"),
            "request_id": resp.get("request_id", ""),
            "response": resp,
            "record": record,
            "degraded_to_deterministic": degraded,
        })
        if (i + 1) % 10 == 0 or i == len(dataset) - 1:
            logger.info("  agentic pass %d/%d", i + 1, len(dataset))
    return results


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def _first_expected_rank(expected: list[str], retrieved: list[str]) -> int | None:
    """1-based rank of the first expected id in the retrieved list."""
    expected_set = {str(e) for e in expected}
    for idx, rid in enumerate(retrieved, start=1):
        if str(rid) in expected_set:
            return idx
    return None


def _relative_improvement_pct(baseline: float, candidate: float) -> float:
    if baseline == 0:
        return 100.0 if candidate > 0 else 0.0
    return (candidate - baseline) / baseline * 100.0


def _flatten_latency(metrics: dict[str, Any]) -> dict[str, Any]:
    metrics["p50_latency_ms"] = metrics["latency_ms"]["p50"]
    metrics["p95_latency_ms"] = metrics["latency_ms"]["p95"]
    return metrics


def build_comparison_report(
    *,
    dataset: list[dict[str, Any]],
    baseline_results: list[dict[str, Any]],
    agentic_results: list[dict[str, Any]],
    hard_metrics: dict[str, Any],
    top_k: int,
    baseline_query_count_001: int = _001_QUERY_COUNT,
    repeatability: dict[str, Any] | None = None,
    cold_agentic: list[dict[str, Any]] | None = None,
    cold_baseline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the agentic comparison report with the three-gate decision."""
    n = len(dataset)
    baseline_metrics = _flatten_latency(compute_metrics(baseline_results, top_k))
    agentic_metrics = _flatten_latency(compute_metrics(agentic_results, top_k))
    agentic_metrics["total_cost"] = round(sum(
        float((r.get("record") or {}).get("total_cost") or 0.0)
        for r in agentic_results
    ), 6)

    # Cold-pass override (T069/T070/T072): latency, cost, token estimate and
    # the degraded-to-deterministic count reflect REAL LLM calls; the
    # Recall/MRR/nDCG metrics and rankings stay from the deterministic warm
    # metric passes.
    if cold_agentic is not None and len(cold_agentic) == n:
        cold_metrics = _flatten_latency(compute_metrics(cold_agentic, top_k))
        agentic_metrics["latency_ms"] = cold_metrics["latency_ms"]
        agentic_metrics["p50_latency_ms"] = cold_metrics["p50_latency_ms"]
        agentic_metrics["p95_latency_ms"] = cold_metrics["p95_latency_ms"]
        agentic_metrics["total_cost"] = round(sum(
            float((r.get("record") or {}).get("total_cost") or 0.0)
            for r in cold_agentic
        ), 6)
        agentic_metrics["estimated_llm_tokens"] = round(sum(
            float((r.get("record") or {}).get("total_llm_tokens") or 0.0)
            for r in cold_agentic
        ), 2)
    if cold_baseline is not None and len(cold_baseline) == n:
        cold_b = _flatten_latency(compute_metrics(cold_baseline, top_k))
        baseline_metrics["latency_ms"] = cold_b["latency_ms"]
        baseline_metrics["p50_latency_ms"] = cold_b["p50_latency_ms"]
        baseline_metrics["p95_latency_ms"] = cold_b["p95_latency_ms"]

    # Subset indices
    idx_001 = list(range(min(baseline_query_count_001, n)))
    beneficiary_idx = [i for i in range(n) if _is_beneficiary(dataset[i])]
    nonbeneficiary_idx = [
        i for i in range(n)
        if i >= baseline_query_count_001 and not _is_beneficiary(dataset[i])
    ]

    def _per_query(indices):
        b_sub = [baseline_results[i] for i in indices]
        a_sub = [agentic_results[i] for i in indices]
        return (
            compute_metrics(b_sub, top_k) if b_sub else None,
            compute_metrics(a_sub, top_k) if a_sub else None,
            b_sub, a_sub,
        )

    # --- SC-001: beneficiary subset relative improvement ----------------
    b_ben, a_ben, _, _ = _per_query(beneficiary_idx)
    if b_ben is not None and a_ben is not None:
        rel_mrr = _relative_improvement_pct(b_ben["mrr"]["mean"], a_ben["mrr"]["mean"])
        rel_ndcg = _relative_improvement_pct(
            b_ben["ndcg_at_k"]["mean"], a_ben["ndcg_at_k"]["mean"],
        )
        recall_ok = (
            a_ben["recall_at_k"]["mean"] >= b_ben["recall_at_k"]["mean"] - 1e-9
        )
        sc001_pass = max(rel_mrr, rel_ndcg) >= SC001_THRESHOLD_PCT and recall_ok
    else:
        rel_mrr = rel_ndcg = 0.0
        sc001_pass = False

    # --- SC-002: 001 baseline non-regression ----------------------------
    sc002_pass = True
    sc002_notes = []
    for i in idx_001:
        b = baseline_results[i]
        a = agentic_results[i]
        exp = b["expected_evidence_ids"]
        rb = compute_recall_at_k(exp, b["retrieved_evidence_ids"], top_k)
        ra = compute_recall_at_k(exp, a["retrieved_evidence_ids"], top_k)
        mb, ma = compute_mrr(exp, b["retrieved_evidence_ids"]), compute_mrr(exp, a["retrieved_evidence_ids"])
        nb, na = compute_ndcg_at_k(exp, b["retrieved_evidence_ids"], top_k), compute_ndcg_at_k(exp, a["retrieved_evidence_ids"], top_k)
        if abs(ra - rb) > 1e-9:
            sc002_pass = False
            sc002_notes.append(f"query {i}: recall {rb} -> {ra}")
        if ma < mb - SC_TOLERANCE or na < nb - SC_TOLERANCE:
            sc002_pass = False
            sc002_notes.append(f"query {i}: MRR/nDCG regression")

    # --- SC-015: 002/004 non-beneficiary non-regression ------------------
    sc015_pass = True
    sc015_notes = []
    for i in nonbeneficiary_idx:
        b = baseline_results[i]
        a = agentic_results[i]
        exp = b["expected_evidence_ids"]
        rb = compute_recall_at_k(exp, b["retrieved_evidence_ids"], top_k)
        ra = compute_recall_at_k(exp, a["retrieved_evidence_ids"], top_k)
        mb, ma = compute_mrr(exp, b["retrieved_evidence_ids"]), compute_mrr(exp, a["retrieved_evidence_ids"])
        nb, na = compute_ndcg_at_k(exp, b["retrieved_evidence_ids"], top_k), compute_ndcg_at_k(exp, a["retrieved_evidence_ids"], top_k)
        if ra < rb - 1e-9:
            sc015_pass = False
            sc015_notes.append(f"query {i}: recall {rb} -> {ra}")
        if ma < mb - SC_TOLERANCE or na < nb - SC_TOLERANCE:
            sc015_pass = False
            sc015_notes.append(f"query {i}: MRR/nDCG regression")

    # --- Hard metrics -----------------------------------------------------
    leakage = int(hard_metrics.get("cross_project_leakage_events", 0))
    schema_rate = float(hard_metrics.get("schema_validity_rate", 0.0))
    locate_rate = float(hard_metrics.get("source_locatability_rate", 0.0))
    hard_pass = leakage == 0 and schema_rate >= 1.0 and locate_rate >= 1.0

    # --- Per-query comparison ---------------------------------------------
    per_query: list[dict[str, Any]] = []
    for i in range(n):
        b = baseline_results[i]
        a = agentic_results[i]
        record = a.get("record") or {}
        agent_outputs = record.get("agent_outputs_ref", {}) if record else {}
        planner_ref = agent_outputs.get("query_planner", {})
        analyst_ref = agent_outputs.get("evidence_analyst", {})
        orch_ref = agent_outputs.get("context_orchestrator", {})
        per_query.append({
            "query_index": i,
            "query": dataset[i].get("query", ""),
            "category": dataset[i].get("category"),
            "is_beneficiary": _is_beneficiary(dataset[i]),
            "project_scope": dataset[i].get("project_scope", []),
            "expected_evidence_ids": b["expected_evidence_ids"],
            "baseline_evidence_ids": b["retrieved_evidence_ids"],
            "agentic_evidence_ids": a["retrieved_evidence_ids"],
            "baseline_rank": _first_expected_rank(b["expected_evidence_ids"], b["retrieved_evidence_ids"]),
            "agentic_rank": _first_expected_rank(a["expected_evidence_ids"], a["retrieved_evidence_ids"]),
            "baseline_latency_ms": round(
                (cold_baseline[i]["latency_ms"]
                 if cold_baseline is not None and i < len(cold_baseline)
                 else b["latency_ms"]), 2,
            ),
            "agentic_latency_ms": round(
                (cold_agentic[i]["latency_ms"]
                 if cold_agentic is not None and i < len(cold_agentic)
                 else a["latency_ms"]), 2,
            ),
            "agent_judgment": {
                "sub_problems": planner_ref.get("sub_problems", []),
                "judgment_ids": analyst_ref.get("judgment_ids", []),
                "rounds_completed": record.get("rounds_completed", 0) if record else 0,
                "completion_status": record.get("completion_status", a.get("status")),
                "selection_decisions": [
                    s.get("decision") for s in orch_ref.get("selection_list", [])
                ],
                "schema_valid_all": record.get("schema_valid_all", True) if record else True,
            },
            "sub_path_timings": record.get("sub_path_timings", {}) if record else {},
            "ledger_ref": {
                "request_id": a.get("request_id", ""),
                "ledger_entry_ids": (record.get("ledger_ref", {}) or {}).get(
                    "ledger_entry_ids", []
                ) if record else [],
            },
        })

    runner = AgenticComparisonRunner()
    report = runner.build_report(
        baseline_metrics=baseline_metrics,
        agentic_metrics=agentic_metrics,
        per_query_data=per_query,
        beneficiary_subset_improvement=max(rel_mrr, rel_ndcg) / 100.0,
        baseline_non_regression=sc002_pass,
        enhanced_non_regression=sc015_pass,
        hard_metrics_pass=hard_pass,
    )
    report["three_gate_pass"] = {
        "sc001_pass": sc001_pass,
        "sc001_relative_mrr_improvement_pct": round(rel_mrr, 4),
        "sc001_relative_ndcg_improvement_pct": round(rel_ndcg, 4),
        "sc002_pass": sc002_pass,
        "sc002_notes": sc002_notes,
        "sc015_pass": sc015_pass,
        "sc015_notes": sc015_notes,
        "hard_metrics_pass": hard_pass,
        "hard_metrics": hard_metrics,
        "all_passed": sc001_pass and sc002_pass and sc015_pass and hard_pass,
    }
    report["enters_default_path"] = report["three_gate_pass"]["all_passed"]
    report["dataset_size"] = n
    report["beneficiary_subset_size"] = len(beneficiary_idx)
    report["agentic_degraded_queries"] = sum(
        1 for r in (cold_agentic if cold_agentic is not None else agentic_results)
        if r.get("degraded_to_deterministic")
    )
    report["subset_indices"] = {
        "baseline_001": idx_001,
        "beneficiary": beneficiary_idx,
        "non_beneficiary": nonbeneficiary_idx,
    }
    report["reproducibility"] = repeatability or {
        "tolerance": REPEAT_TOLERANCE,
        "latency_sensitive": True,
        "reproducible": None,
    }
    report["measurement"] = {
        "metrics_source": "deterministic warm metric passes (LLM response-cache replay)",
        "latency_cost_source": "cold pass with real LLM calls and real guardrails",
        "fairness": "each pass reruns the deterministic baseline before the agentic path (FR-030)",
        "reproducibility_source": "metric pass 1 vs metric pass 2 (SC-008, 1% tolerance)",
    }
    return report


# ---------------------------------------------------------------------------
# Hard-constraint measurement (leakage / schema / locatability / ledger)
# ---------------------------------------------------------------------------


def _load_mcp_search_schema() -> dict[str, Any]:
    schema_path = (
        _REPO_ROOT / "specs" / "001-minimum-rag-mcp-loop" / "contracts"
        / "mcp-search-output.schema.json"
    )
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


async def measure_agentic_hard_constraints(
    dataset: list[dict[str, Any]],
    agentic_results: list[dict[str, Any]],
    session_factory,
) -> dict[str, Any]:
    """Leakage=0, schema validity=100%, source locatability=100% (SC-003/004/006)."""
    import jsonschema

    schema = _load_mcp_search_schema()
    leakage_events = 0
    valid_responses = 0
    total_items = 0
    locatable_items = 0
    ledger_total = 0
    ledger_resolved = 0

    chunk_ids: set[int] = set()
    for res in agentic_results:
        for eid in res["retrieved_evidence_ids"]:
            try:
                chunk_ids.add(int(eid))
            except (TypeError, ValueError):
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

    validator = jsonschema.Draft202012Validator(schema)
    for i, res in enumerate(agentic_results):
        requested = {str(s) for s in dataset[i].get("project_scope", [])}
        for scope in res["scopes"]:
            if scope and str(scope) not in requested:
                leakage_events += 1

        response = res.get("response")
        if response is not None and not list(validator.iter_errors(response)):
            valid_responses += 1

        request_id = res.get("request_id", "")
        record = res.get("record") or {}
        for eid in res["retrieved_evidence_ids"]:
            total_items += 1
            try:
                if position_map.get(int(eid)):
                    locatable_items += 1
            except (TypeError, ValueError):
                pass
            # (request_id, evidence_id) ledger bridge (SC-006) — resolvable
            # whenever a run record exists for the request.
            if record:
                ledger_total += 1
                ledger_ids = (record.get("ledger_ref", {}) or {}).get("ledger_entry_ids", [])
                if request_id and ledger_ids:
                    ledger_resolved += 1

    n_responses = max(1, len(agentic_results))
    return {
        "cross_project_leakage_events": leakage_events,
        "schema_validity_rate": round(valid_responses / n_responses, 4),
        "source_locatability_rate": round(
            locatable_items / total_items, 4
        ) if total_items else 1.0,
        "ledger_bridge_rate": round(
            ledger_resolved / ledger_total, 4
        ) if ledger_total else 1.0,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Agentic comparison evaluation runner (005, T061).",
    )
    parser.add_argument("--dataset", default=str(_REPO_ROOT / "eval" / "eval_dataset.json"))
    parser.add_argument(
        "--agentic-dataset",
        default=str(_REPO_ROOT / "eval" / "agentic_eval_dataset.json"),
    )
    parser.add_argument(
        "--output",
        default=str(_REPO_ROOT / "eval" / "agentic_comparison_report.json"),
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only evaluate the first N combined entries (smoke runs).",
    )
    parser.add_argument(
        "--skip-repeatability", action="store_true",
        help="Skip the second pass used for the SC-008 reproducibility check.",
    )
    parser.add_argument(
        "--llm-cache-dir",
        default=str(_REPO_ROOT / "eval" / ".agentic_llm_cache"),
        help="Directory for the LLM response cache (T070, SC-008).",
    )
    parser.add_argument(
        "--keep-llm-cache", action="store_true",
        help="Reuse an existing cache instead of clearing it at start.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # --- Evaluation environment (T069/T070/T072) ----------------------
    # Within-guardrail runtime overrides that measure the agentic
    # mechanism while the deterministic default path stays untouched
    # (FR-006/FR-007). Node timeout raised to the documented guardrail
    # limit (deepseek calls can exceed the 5s default); the per-source
    # cap matches the 001 deterministic path (5) so recall comparisons
    # are apples-to-apples. User-provided values win (setdefault).
    os.environ.setdefault("AGENTIC_NODE_TIMEOUT_MS", "10000")
    os.environ.setdefault("AGENTIC_MAX_EVIDENCE_PER_SOURCE", "5")
    cache_dir = Path(args.llm_cache_dir)
    if not args.keep_llm_cache and cache_dir.exists():
        try:
            shutil.rmtree(cache_dir)
        except OSError as exc:
            # A locked cache dir (e.g. a lingering handle) must not kill the
            # run: fall back to a fresh timestamped subdir instead.
            logger.warning("Could not clear cache dir (%s); using a fresh dir", exc)
            cache_dir = cache_dir / f"run-{int(time.time())}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["AGENTIC_LLM_CACHE_PATH"] = str(cache_dir)
    logger.info("LLM response cache: %s", cache_dir)

    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    settings = get_settings()
    dataset = load_combined_dataset(args.dataset, args.agentic_dataset)
    if args.limit is not None:
        dataset = dataset[: args.limit]
    logger.info("Combined dataset: %d entries", len(dataset))

    engine = create_async_engine(settings.database_url, echo=False)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def session_factory():
        async with maker() as session:
            yield session

    # Prepare the evaluation corpus: graph edges for java/ddl scopes (the
    # user-triggered rebuild declaration, FR-027 idempotent) and Qdrant
    # vectors (reindex when missing, blueprint §8.4).
    scope_ids = sorted({int(s) for e in dataset for s in e.get("project_scope", [])})
    triples = await ensure_graph_corpus(session_factory, scope_ids)
    logger.info("Graph corpus ready for %d scopes", len(triples))

    store = QdrantStore()
    from rag_mcp.services.ingestion_service import _derive_index_version
    collection = f"chunks_hybrid_{_derive_index_version(settings.embedding_model)}"
    if not store.collection_exists(collection):
        logger.info("Hybrid collection missing — reindexing eval corpus")
        import reindex_eval_qdrant
        await reindex_eval_qdrant.reindex(args.dataset)

    embedding_provider = _EvalEmbeddingProvider(settings.embedding_model)
    reranker = None  # parity: both paths run without reranker in eval

    baseline_fn = lambda entries: run_deterministic_pass(  # noqa: E731
        entries, session_factory, store, embedding_provider, reranker, args.top_k,
    )
    agentic_fn = lambda entries: run_agentic_pass(  # noqa: E731
        entries, session_factory, store, embedding_provider, reranker, args.top_k,
    )

    # 1. COLD pass: real latency / cost / degradation (fair, FR-030).
    #    Also populates the LLM response cache as a side effect.
    logger.info("Cold pass (real latency/cost): baseline rerun, then agentic (FR-030)")
    cold_baseline, cold_agentic = await run_fair_paths(
        dataset, baseline_fn, agentic_fn,
    )

    # 2. Warm-up completion pass: replay the cache and fill any gaps left
    #    by cold-pass degradations so the metric passes are fully warm.
    logger.info("Warm-up completion pass (LLM cache gap fill)")
    await agentic_fn(dataset)

    # 3. Metric pass 1: deterministic warm replay (the ranking source).
    logger.info("Metric pass 1: baseline + agentic (deterministic warm)")
    baseline_results, agentic_results = await run_fair_paths(
        dataset, baseline_fn, agentic_fn,
    )

    hard_metrics = await measure_agentic_hard_constraints(
        dataset, agentic_results, session_factory,
    )
    baseline_leakage = 0
    for i, res in enumerate(baseline_results):
        requested = {str(s) for s in dataset[i].get("project_scope", [])}
        for scope in res["scopes"]:
            if scope and str(scope) not in requested:
                baseline_leakage += 1
    hard_metrics["baseline_cross_project_leakage_events"] = baseline_leakage

    repeatability: dict[str, Any] | None = None
    if not args.skip_repeatability:
        # 4. Metric pass 2: reproducibility (byte-identical warm replay).
        logger.info("Metric pass 2: reproducibility rerun (SC-008)")
        baseline_results_2, agentic_results_2 = await run_fair_paths(
            dataset, baseline_fn, agentic_fn,
        )
        m_b1 = compute_metrics(baseline_results, args.top_k)
        m_b2 = compute_metrics(baseline_results_2, args.top_k)
        m_a1 = compute_metrics(agentic_results, args.top_k)
        m_a2 = compute_metrics(agentic_results_2, args.top_k)
        rep_baseline = check_reproducibility(m_b1, m_b2, REPEAT_TOLERANCE)
        rep_agentic = check_reproducibility(m_a1, m_a2, REPEAT_TOLERANCE)
        repeatability = {
            "tolerance": REPEAT_TOLERANCE,
            "latency_sensitive": True,
            "reproducible": rep_baseline["reproducible"] and rep_agentic["reproducible"],
            "baseline": rep_baseline,
            "agentic": rep_agentic,
        }

    report = build_comparison_report(
        dataset=dataset,
        baseline_results=baseline_results,
        agentic_results=agentic_results,
        hard_metrics=hard_metrics,
        top_k=args.top_k,
        baseline_query_count_001=_001_QUERY_COUNT,
        repeatability=repeatability,
        cold_agentic=cold_agentic,
        cold_baseline=cold_baseline,
    )
    report["generated_at"] = datetime.now(timezone.utc).isoformat()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    gate = report["three_gate_pass"]
    logger.info("Report written to %s", output_path)
    logger.info(
        "Gates: SC-001=%s SC-002=%s SC-015=%s hard=%s -> all_passed=%s -> enters_default_path=%s",
        gate["sc001_pass"], gate["sc002_pass"], gate["sc015_pass"],
        gate["hard_metrics_pass"], gate["all_passed"], report["enters_default_path"],
    )
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
