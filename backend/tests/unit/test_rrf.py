"""Unit tests for RRF fusion (T004).

Tests: formula correctness Σ 1/(k+rank), determinism, tie-breaker by chunk_id,
k parameter, empty side preservation.

These tests MUST FAIL before fusion/rrf.py is implemented (TDD).
"""

from __future__ import annotations

import math

import pytest

from rag_mcp.fusion.rrf import rrf_fuse, FusedCandidate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_ranked(chunk_ids: list[str], scores: list[float] | None = None) -> list[dict]:
    """Create a ranked list of retrieval results."""
    if scores is None:
        scores = [1.0 - i * 0.1 for i in range(len(chunk_ids))]
    return [
        {"chunk_id": cid, "score": sc, "payload": {"knowledge_scope_id": "100"}}
        for cid, sc in zip(chunk_ids, scores)
    ]


# ---------------------------------------------------------------------------
# Formula correctness: Σ 1/(k + rank)
# ---------------------------------------------------------------------------

class TestRRFFormula:
    """RRF score must equal Σ 1/(k + rank) for each retriever."""

    def test_dense_only_candidate(self):
        """A candidate that appears only in dense (rank 1) gets 1/(k+1)."""
        dense = _make_ranked(["A", "B", "C"])
        sparse = _make_ranked([])
        k = 60
        results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k)
        # Candidate A is rank 1 in dense → score = 1/(60+1)
        expected = 1.0 / (k + 1)
        a_result = next(r for r in results if r.chunk_id == "A")
        assert math.isclose(a_result.fused_score, expected, rel_tol=1e-9)

    def test_sparse_only_candidate(self):
        """A candidate that appears only in sparse (rank 2) gets 1/(k+2)."""
        dense = _make_ranked([])
        sparse = _make_ranked(["X", "Y", "Z"])
        k = 60
        results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k)
        # Candidate Y is rank 2 in sparse → score = 1/(60+2)
        expected = 1.0 / (k + 2)
        y_result = next(r for r in results if r.chunk_id == "Y")
        assert math.isclose(y_result.fused_score, expected, rel_tol=1e-9)

    def test_both_retrievers_candidate(self):
        """A candidate in both lists: score = 1/(k+rank_dense) + 1/(k+rank_sparse)."""
        dense = _make_ranked(["A", "B"])
        sparse = _make_ranked(["B", "A"])
        k = 60
        results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k)
        # B: dense rank 2, sparse rank 1 → 1/(60+2) + 1/(60+1)
        expected_b = 1.0 / (k + 2) + 1.0 / (k + 1)
        b_result = next(r for r in results if r.chunk_id == "B")
        assert math.isclose(b_result.fused_score, expected_b, rel_tol=1e-9)

    def test_candidate_in_both_higher_than_one_side(self):
        """A candidate in both lists should have higher score than in one list."""
        dense = _make_ranked(["A", "B"])
        sparse = _make_ranked(["B", "C"])
        k = 60
        results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k)
        b_result = next(r for r in results if r.chunk_id == "B")
        a_result = next(r for r in results if r.chunk_id == "A")
        # B appears in both, A only in dense rank 1
        # B: 1/(60+2) + 1/(60+1) > A: 1/(60+1)
        assert b_result.fused_score > a_result.fused_score


# ---------------------------------------------------------------------------
# Determinism (FR-017)
# ---------------------------------------------------------------------------

class TestDeterminism:
    """RRF fusion must be deterministic — no random perturbation."""

    def test_same_input_same_output(self):
        """Same input must produce identical output ordering."""
        dense = _make_ranked(["A", "B", "C"])
        sparse = _make_ranked(["C", "B", "A"])
        results1 = rrf_fuse(dense_results=dense, sparse_results=sparse, k=60)
        results2 = rrf_fuse(dense_results=dense, sparse_results=sparse, k=60)
        assert [r.chunk_id for r in results1] == [r.chunk_id for r in results2]
        assert [r.fused_score for r in results1] == [r.fused_score for r in results2]

    def test_no_random_perturbation_on_ties(self):
        """Ties must be broken deterministically, not randomly."""
        # Create a tie: A and B both have the same fused score
        dense = _make_ranked(["A", "B"])
        sparse = _make_ranked(["B", "A"])
        k = 60
        results1 = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k)
        results2 = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k)
        # Run multiple times — order must be stable
        for _ in range(5):
            results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k)
            assert [r.chunk_id for r in results] == [r.chunk_id for r in results1]


# ---------------------------------------------------------------------------
# Tie-breaker (FR-017): (fused_score_desc, dense_rank_asc, sparse_rank_asc, chunk_id_asc)
# ---------------------------------------------------------------------------

class TestTieBreaker:
    """Tie-breaking must follow (fused_score_desc, dense_rank_asc, sparse_rank_asc, chunk_id_asc)."""

    def test_tie_broken_by_dense_rank(self):
        """When fused scores are equal, lower dense_rank wins."""
        # A: dense rank 1, sparse rank 3 → 1/(60+1) + 1/(60+3)
        # B: dense rank 2, sparse rank 2 → 1/(60+2) + 1/(60+2)
        # These are close but not equal; let's create an actual tie
        # A only in dense rank 1: 1/(60+1)
        # B only in dense rank 1 of a different list... 
        # Actually let's use: A in dense rank 1 only, B in sparse rank 1 only
        # Both get 1/(60+1) = tie
        dense = _make_ranked(["A"])
        sparse = _make_ranked(["B"])
        k = 60
        results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k)
        # Both have score 1/(60+1) → tie
        # Tie-breaker: dense_rank_asc (A has dense_rank=1, B has dense_rank=None)
        # None should sort after non-None (lower rank = better)
        a_result = next(r for r in results if r.chunk_id == "A")
        b_result = next(r for r in results if r.chunk_id == "B")
        assert math.isclose(a_result.fused_score, b_result.fused_score, rel_tol=1e-9)
        # A should come before B (has a dense_rank, B doesn't)
        assert results.index(a_result) < results.index(b_result)

    def test_tie_broken_by_chunk_id(self):
        """When all else is equal, lower chunk_id wins (ascending)."""
        # Two candidates with same score, same dense_rank (None), same sparse_rank (None)
        # But that's impossible unless they're in the same retriever at the same rank
        # Let's use: both only in dense, both rank 1 (impossible in a single list)
        # Instead: A only in sparse rank 1, B only in sparse rank 1 — impossible
        # Use different approach: A in dense rank 1, B in sparse rank 1 → tie
        # Already covered above; for chunk_id tie-break we need same retriever ranks
        # A in dense rank 1 + sparse rank 2, B in dense rank 2 + sparse rank 1
        # Both get 1/(60+1)+1/(60+2) = 1/(60+2)+1/(60+1) → exact tie
        dense = _make_ranked(["A", "B"])
        sparse = _make_ranked(["B", "A"])
        k = 60
        results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k)
        a_result = next(r for r in results if r.chunk_id == "A")
        b_result = next(r for r in results if r.chunk_id == "B")
        # Exact tie → chunk_id ascending: A < B
        assert math.isclose(a_result.fused_score, b_result.fused_score, rel_tol=1e-12)
        assert results.index(a_result) < results.index(b_result), (
            "Tie must be broken by chunk_id ascending (FR-017)"
        )


# ---------------------------------------------------------------------------
# k parameter
# ---------------------------------------------------------------------------

class TestKParameter:
    """RRF k parameter controls rank smoothing."""

    def test_default_k_is_60(self):
        """Default k should be 60."""
        dense = _make_ranked(["A"])
        sparse = _make_ranked([])
        results = rrf_fuse(dense_results=dense, sparse_results=sparse)
        expected = 1.0 / (60 + 1)
        assert math.isclose(results[0].fused_score, expected, rel_tol=1e-9)

    def test_custom_k(self):
        """Custom k should change the fusion scores."""
        dense = _make_ranked(["A"])
        sparse = _make_ranked([])
        k = 10
        results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k)
        expected = 1.0 / (k + 1)
        assert math.isclose(results[0].fused_score, expected, rel_tol=1e-9)

    def test_larger_k_reduces_score_difference(self):
        """Larger k makes rank 1 and rank 2 scores closer (smoother)."""
        dense = _make_ranked(["A", "B"])
        sparse = _make_ranked([])
        k_small = 1
        k_large = 100
        results_small = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k_small)
        results_large = rrf_fuse(dense_results=dense, sparse_results=sparse, k=k_large)
        diff_small = abs(results_small[0].fused_score - results_small[1].fused_score)
        diff_large = abs(results_large[0].fused_score - results_large[1].fused_score)
        assert diff_large < diff_small, "Larger k should reduce score differences"


# ---------------------------------------------------------------------------
# Empty side preservation
# ---------------------------------------------------------------------------

class TestEmptySide:
    """When one side is empty, the other side's results should be preserved."""

    def test_empty_dense_preserves_sparse(self):
        """Empty dense results should preserve all sparse results."""
        dense = _make_ranked([])
        sparse = _make_ranked(["X", "Y", "Z"])
        results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=60)
        assert len(results) == 3
        chunk_ids = {r.chunk_id for r in results}
        assert chunk_ids == {"X", "Y", "Z"}

    def test_empty_sparse_preserves_dense(self):
        """Empty sparse results should preserve all dense results."""
        dense = _make_ranked(["A", "B", "C"])
        sparse = _make_ranked([])
        results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=60)
        assert len(results) == 3
        chunk_ids = {r.chunk_id for r in results}
        assert chunk_ids == {"A", "B", "C"}

    def test_both_empty_returns_empty(self):
        """Both sides empty should return an empty list."""
        results = rrf_fuse(dense_results=[], sparse_results=[], k=60)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Result ordering
# ---------------------------------------------------------------------------

class TestOrdering:
    """Results must be sorted by fused_score descending (with deterministic tie-break)."""

    def test_results_sorted_by_fused_score_desc(self):
        """Fused results must be sorted by fused_score descending."""
        dense = _make_ranked(["A", "B", "C", "D"])
        sparse = _make_ranked(["C", "A", "E"])
        results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=60)
        scores = [r.fused_score for r in results]
        assert scores == sorted(scores, reverse=True), (
            "Results must be sorted by fused_score descending"
        )

    def test_source_retrievers_tracked(self):
        """Each FusedCandidate must track which retrievers found it."""
        dense = _make_ranked(["A", "B"])
        sparse = _make_ranked(["B", "C"])
        results = rrf_fuse(dense_results=dense, sparse_results=sparse, k=60)
        a = next(r for r in results if r.chunk_id == "A")
        b = next(r for r in results if r.chunk_id == "B")
        c = next(r for r in results if r.chunk_id == "C")
        assert "dense" in a.source_retrievers
        assert "sparse" not in a.source_retrievers
        assert "dense" in b.source_retrievers
        assert "sparse" in b.source_retrievers
        assert "sparse" in c.source_retrievers
        assert "dense" not in c.source_retrievers
