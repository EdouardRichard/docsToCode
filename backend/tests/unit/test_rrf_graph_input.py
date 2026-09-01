"""Unit test for RRF fusion with graph as 3rd retriever input (T013).

Validates three-way RRF (Dense+Sparse+graph), graph_rank contribution,
structure_weight only for internal ordering (not fusion coefficient),
and determinism (FR-019, research sec 2).

This test MUST FAIL before the graph input is added to rrf_fuse (TDD).
"""

from __future__ import annotations

import math

import pytest

from rag_mcp.fusion.rrf import FusedCandidate, rrf_fuse


def _make_ranked(chunk_ids, scores=None):
    if scores is None:
        scores = [1.0 - i * 0.1 for i in range(len(chunk_ids))]
    return [
        {"chunk_id": cid, "score": sc, "payload": {"knowledge_scope_id": "100"}}
        for cid, sc in zip(chunk_ids, scores)
    ]


def _make_graph_candidates(chunk_ids, weights=None):
    """Make graph candidate dicts with chunk_id and graph_rank (1-based)."""
    if weights is None:
        weights = [1.0 - i * 0.1 for i in range(len(chunk_ids))]
    return [
        {"chunk_id": cid, "graph_rank": i + 1, "structure_weight": w}
        for i, (cid, w) in enumerate(zip(chunk_ids, weights))
    ]


class TestThreeWayRRF:
    def test_graph_only_candidate(self):
        """A candidate only in graph (rank 1) gets 1/(k+1)."""
        dense = _make_ranked([])
        sparse = _make_ranked([])
        graph = _make_graph_candidates(["G1"])
        k = 60
        results = rrf_fuse(dense, sparse, k=k, graph_results=graph)
        g = next(r for r in results if r.chunk_id == "G1")
        expected = 1.0 / (k + 1)
        assert math.isclose(g.fused_score, expected, rel_tol=1e-9)
        assert "graph" in g.source_retrievers

    def test_all_three_retrievers(self):
        """Candidate in all three: 1/(k+rd) + 1/(k+rs) + 1/(k+rg)."""
        dense = _make_ranked(["A"])  # rank 1
        sparse = _make_ranked(["A"])  # rank 1
        graph = _make_graph_candidates(["A"])  # rank 1
        k = 60
        results = rrf_fuse(dense, sparse, k=k, graph_results=graph)
        a = next(r for r in results if r.chunk_id == "A")
        expected = 1.0 / (k + 1) + 1.0 / (k + 1) + 1.0 / (k + 1)
        assert math.isclose(a.fused_score, expected, rel_tol=1e-9)
        assert "dense" in a.source_retrievers
        assert "sparse" in a.source_retrievers
        assert "graph" in a.source_retrievers

    def test_graph_contributes_to_score(self):
        """Graph input MUST increase a candidate's fused score."""
        dense = _make_ranked(["A"])
        sparse = _make_ranked([])
        k = 60
        without_graph = rrf_fuse(dense, sparse, k=k)
        with_graph = rrf_fuse(dense, sparse, k=k,
                              graph_results=_make_graph_candidates(["A"]))
        a_wo = next(r for r in without_graph if r.chunk_id == "A")
        a_w = next(r for r in with_graph if r.chunk_id == "A")
        assert a_w.fused_score > a_wo.fused_score

    def test_structure_weight_not_fusion_coefficient(self):
        """Structure weight MUST NOT be an independent fusion coefficient.

        Two graph candidates with different weights but same graph_rank
        MUST have the same fused_score (weight only affects internal ranking).
        """
        graph = [
            {"chunk_id": "X", "graph_rank": 1, "structure_weight": 1.0},
            {"chunk_id": "Y", "graph_rank": 1, "structure_weight": 0.3},
        ]
        results = rrf_fuse([], [], k=60, graph_results=graph)
        x = next(r for r in results if r.chunk_id == "X")
        y = next(r for r in results if r.chunk_id == "Y")
        # Both rank 1 -> same RRF contribution (weight irrelevant to fusion)
        assert math.isclose(x.fused_score, y.fused_score, rel_tol=1e-9)


class TestDeterminism:
    def test_same_input_same_output(self):
        dense = _make_ranked(["A", "B"])
        sparse = _make_ranked(["B", "C"])
        graph = _make_graph_candidates(["A", "C"])
        r1 = rrf_fuse(dense, sparse, k=60, graph_results=graph)
        r2 = rrf_fuse(dense, sparse, k=60, graph_results=graph)
        assert [r.chunk_id for r in r1] == [r.chunk_id for r in r2]
        assert [r.fused_score for r in r1] == [r.fused_score for r in r2]

    def test_no_random_perturbation(self):
        dense = _make_ranked(["A"])
        sparse = _make_ranked(["B"])
        graph = _make_graph_candidates(["C"])
        for _ in range(5):
            results = rrf_fuse(dense, sparse, k=60, graph_results=graph)
            assert [r.chunk_id for r in results] == sorted(["A", "B", "C"])


class TestBackwardCompatibility:
    def test_no_graph_results_works(self):
        """Calling without graph_results MUST still work (backward compat)."""
        dense = _make_ranked(["A", "B"])
        sparse = _make_ranked(["B", "C"])
        results = rrf_fuse(dense, sparse, k=60)
        assert len(results) == 3
        # No graph source_retrievers
        for r in results:
            assert "graph" not in r.source_retrievers

    def test_empty_graph_results(self):
        """Empty graph_results MUST not change scores."""
        dense = _make_ranked(["A"])
        sparse = _make_ranked([])
        without = rrf_fuse(dense, sparse, k=60)
        with_empty = rrf_fuse(dense, sparse, k=60, graph_results=[])
        assert len(without) == len(with_empty)
        assert without[0].fused_score == with_empty[0].fused_score


class TestFusedCandidateFields:
    def test_graph_rank_field(self):
        """FusedCandidate MUST have graph_rank field."""
        graph = _make_graph_candidates(["A"])
        results = rrf_fuse([], [], k=60, graph_results=graph)
        a = results[0]
        assert hasattr(a, "graph_rank")
        assert a.graph_rank == 1

    def test_graph_structure_weight_field(self):
        """FusedCandidate MUST have graph_structure_weight field."""
        graph = [{"chunk_id": "A", "graph_rank": 1, "structure_weight": 0.5}]
        results = rrf_fuse([], [], k=60, graph_results=graph)
        a = results[0]
        assert hasattr(a, "graph_structure_weight")
        assert a.graph_structure_weight == 0.5
