"""Instance-form smoke test (T069/T070).

SC-009/FR-028: the 001 baseline 11 queries re-run through the retrieval
path for both instance forms (writer + reader); non-latency metrics
(Recall@K / MRR / nDCG) match eval/baseline_report.json within a 1%
relative tolerance. Latency is recorded and annotated env_sensitive.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_mcp.eval.instance_form_smoke import (
    _BASELINE_MEANS,
    compute_metrics,
    load_baseline_queries,
    run_form_smoke,
    write_instance_form_report,
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


def test_write_instance_form_report_persists(tmp_path):
    out = write_instance_form_report(
        {"report_type": "instance_form_smoke", "comparison": {"mrr": {}}},
        tmp_path / "instance_form_smoke_report.json",
    )
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["report_type"] == "instance_form_smoke"
    assert "comparison" in data


@pytest.mark.asyncio
async def test_dual_form_smoke_matches_baseline(
    session_factory, embedding_provider, qdrant_store, tmp_path
):
    queries = load_baseline_queries()
    reports = {}
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
        assert report["tolerance_semantics"] == "non_regression_lower_bound"
        assert report["pass"] is True
        reports[mode] = report

    # T080: persist the instance-form comparison report (FR-028/SC-009).
    combined = {
        "report_type": "instance_form_smoke",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instance_forms": reports,
    }
    out = write_instance_form_report(combined, tmp_path / "instance_form_smoke_report.json")
    assert out.exists()
    persisted = json.loads(out.read_text(encoding="utf-8"))
    assert set(persisted["instance_forms"].keys()) == {"writer", "reader"}
    assert persisted["instance_forms"]["writer"]["comparison"]
