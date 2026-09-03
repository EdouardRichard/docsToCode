"""Tests for parallel sub-problem retrieval (T069, FR-005, US1-AC1).

FR-005 / blueprint sec 12 step 4: retrieval paths run PARALLEL. The
retrieval pipeline must issue the per-sub-problem recalls concurrently
(asyncio.gather), not sequentially: latency for a decomposed query is
the slowest path, not the sum of paths.

Also: a single failing sub-problem path must NOT abort the whole round
(failed-path isolation, blueprint sec 19 degradation semantics) — the
remaining sub-problems still contribute their candidates.

This test MUST FAIL before parallelization exists (TDD Red).
"""

from __future__ import annotations

import asyncio
import time

import pytest

import rag_mcp.orchestration.retrieval_pipeline as pipeline_module
from rag_mcp.orchestration.retrieval_pipeline import AgenticRetrievalPipeline


class _FakeSession:
    def __init__(self) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _session_factory():
    return _FakeSession()


class _FakeRetrievalService:
    """Records concurrency; sleeps to make sequential vs parallel visible."""

    sleep_s: float = 0.15
    instances: list["_FakeRetrievalService"] = []

    def __init__(self, session=None, qdrant_store=None, embedding_provider=None, reranker=None):
        self.concurrent = 0
        type(self).instances.append(self)

    async def recall_candidates(self, query, scope_ids, limit=20, use_graph=False,
                                graph_relation_types=None, graph_hop=None):
        cls = type(self)
        cls.active = getattr(cls, "active", 0) + 1
        cls.max_concurrent = max(getattr(cls, "max_concurrent", 0), cls.active)
        await asyncio.sleep(cls.sleep_s)
        cls.active -= 1
        if query == "fail-me":
            raise RuntimeError("recall boom")
        return {
            "candidates": [{
                "evidence_id": f"c-{query}",
                "chunk_id": f"c-{query}",
                "score": 0.5,
                "payload": {"chunk_id": f"c-{query}", "version_id": 1},
            }],
            "subpath_timings": {"dense_recall_ms": 1.0},
            "failed_paths": [],
            "graph_used": False,
        }


@pytest.fixture()
def fake_service(monkeypatch):
    _FakeRetrievalService.instances = []
    _FakeRetrievalService.active = 0
    _FakeRetrievalService.max_concurrent = 0
    monkeypatch.setattr(
        pipeline_module, "RetrievalService", _FakeRetrievalService,
    )


def _pipeline() -> AgenticRetrievalPipeline:
    p = AgenticRetrievalPipeline(
        session_factory=_session_factory,
        qdrant_store=object(),
        embedding_provider=object(),
        reranker=None,
    )
    return p


def _sub_problems(n: int) -> list[dict]:
    return [
        {"sub_problem_id": i + 1, "query": f"sub-{i}", "signals": ["dense"]}
        for i in range(n)
    ]


class TestParallelRecall:
    def test_sub_problem_recalls_run_concurrently(self, fake_service):
        async def run():
            p = _pipeline()
            async def fake_enrich(session, merged, scope_ids):
                return merged
            p._enrich_candidates = fake_enrich
            return await p.retrieve_round(
                sub_problems=_sub_problems(3), scope_ids=[100], round_index=0,
            )

        start = time.perf_counter()
        result = asyncio.run(run())
        elapsed = time.perf_counter() - start

        assert len(result["candidates"]) == 3
        assert _FakeRetrievalService.max_concurrent == 3, (
            "all sub-problem recalls must run concurrently (FR-005 parallel step 4)"
        )
        assert elapsed < 3 * _FakeRetrievalService.sleep_s, (
            "parallel recall: total time is the slowest path, not the sum"
        )

    def test_single_failing_path_does_not_abort_round(self, fake_service):
        async def run():
            p = _pipeline()
            async def fake_enrich(session, merged, scope_ids):
                return merged
            p._enrich_candidates = fake_enrich
            return await p.retrieve_round(
                sub_problems=[
                    {"sub_problem_id": 1, "query": "fail-me", "signals": ["dense"]},
                    {"sub_problem_id": 2, "query": "good", "signals": ["dense"]},
                ],
                scope_ids=[100],
                round_index=0,
            )

        result = asyncio.run(run())
        assert len(result["candidates"]) == 1, (
            "healthy sub-problem candidates must survive a sibling failure"
        )
        assert result["failed_paths"], "the failing path must be recorded"

    def test_timings_accumulate_across_paths(self, fake_service):
        async def run():
            p = _pipeline()
            async def fake_enrich(session, merged, scope_ids):
                return merged
            p._enrich_candidates = fake_enrich
            return await p.retrieve_round(
                sub_problems=_sub_problems(2), scope_ids=[100], round_index=0,
            )

        result = asyncio.run(run())
        assert result["subpath_timings"]["dense_recall_ms"] == pytest.approx(2.0), (
            "per-path timings accumulate across sub-problems"
        )
