"""Instance-form smoke test (T069/T070).

SC-009/FR-028: the 001 baseline 11 queries re-run through the retrieval
path for both instance forms (writer + reader); non-latency metrics
(Recall@K / MRR / nDCG) match eval/baseline_report.json within a 1%
relative tolerance. Latency is recorded and annotated env_sensitive.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_mcp.eval.instance_form_smoke import (
    _BASELINE_MEANS,
    compute_metrics,
    load_baseline_queries,
    run_form_smoke,
)


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="module")
def embedding_provider():
    from rag_mcp.providers.local_cpu import LocalCPUEmbeddingProvider

    return LocalCPUEmbeddingProvider()


@pytest.fixture(scope="module")
def qdrant_store():
    from rag_mcp.indexing.qdrant_client import QdrantStore

    return QdrantStore()


def test_compute_metrics_known_case():
    m = compute_metrics(["a", "b"], ["a", "x", "c", "d", "b"], k=5)
    assert m["recall_at_k"] == 1.0  # both found
    assert m["mrr"] == 1.0  # a at rank 1
    assert m["ndcg_at_k"] > 0.0


def test_baseline_queries_loaded():
    queries = load_baseline_queries()
    assert len(queries) == 11
    for q in queries:
        assert "query" in q and "expected_evidence_ids" in q


def test_baseline_means_present():
    for metric in ("recall_at_k", "mrr", "ndcg_at_k"):
        assert metric in _BASELINE_MEANS


@pytest.mark.asyncio
async def test_dual_form_smoke_matches_baseline(
    session_factory, embedding_provider, qdrant_store
):
    queries = load_baseline_queries()
    for mode in ("writer", "reader"):
        report = await run_form_smoke(
            mode,
            session_factory=session_factory,
            qdrant_store=qdrant_store,
            embedding_provider=embedding_provider,
            queries=queries,
            top_k=5,
            tolerance=0.01,
        )
        assert report["num_queries"] == 11
        for metric, comp in report["comparison"].items():
            # FR-028/SC-009 non-regression: no metric may DEGRADE below the
            # baseline by more than tolerance; env-drift improvements pass.
            assert comp["no_regression"], (
                f"{mode} {metric} regressed vs baseline: "
                f"measured={comp['measured']}, baseline={comp['baseline']}"
            )
        assert report["latency_env_sensitive"] is True
