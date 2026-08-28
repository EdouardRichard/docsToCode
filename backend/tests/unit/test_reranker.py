"""Contract tests for LocalCPUReranker (T006).

Tests: returns rerank_score, respects top_k, deterministic tie-break,
no random perturbation.

These tests MUST FAIL before local_cpu_reranker.py is implemented (TDD).
Uses a mock CrossEncoder to avoid loading the real model.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rag_mcp.providers.local_cpu_reranker import LocalCPUReranker


# ---------------------------------------------------------------------------
# Mock CrossEncoder factory
# ---------------------------------------------------------------------------

class _MockCrossEncoder:
    """Deterministic mock CrossEncoder for testing.

    Returns a score based on the length similarity between query and candidate
    text, ensuring deterministic and reproducible results.
    """

    def __init__(self, *args, **kwargs):
        self._call_count = 0

    def predict(self, pairs, **kwargs):
        """Return deterministic scores for query-passage pairs."""
        scores = []
        for query, passage in pairs:
            # Deterministic score: based on string overlap
            q_words = set(query.lower().split())
            p_words = set(passage.lower().split())
            overlap = len(q_words & p_words)
            total = len(q_words | p_words) or 1
            score = float(overlap) / total
            scores.append(score)
        return scores


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def reranker():
    """A LocalCPUReranker with mocked CrossEncoder."""
    with patch("rag_mcp.providers.local_cpu_reranker.LocalCPUReranker._load_model") as mock_load:
        mock_model = _MockCrossEncoder()
        mock_load.return_value = mock_model
        r = LocalCPUReranker()
        r._model = mock_model
        return r


def _make_candidates(n: int, prefix: str = "chunk") -> list[dict]:
    """Create N candidate dicts with text and chunk_id."""
    return [
        {
            "chunk_id": f"{prefix}_{i}",
            "content_text": f"This is passage {i} about topic {i}",
            "fused_score": 0.05 - i * 0.001,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Returns rerank_score (contract)
# ---------------------------------------------------------------------------

class TestRerankScore:
    """Reranker must return candidates with 'rerank_score' key."""

    @pytest.mark.asyncio
    async def test_returns_rerank_score(self, reranker):
        """Each returned candidate must have a 'rerank_score' key."""
        candidates = _make_candidates(3)
        results = await reranker.rerank(
            query="topic 0",
            candidates=candidates,
            top_k=2,
        )
        assert len(results) > 0
        for r in results:
            assert "rerank_score" in r, "Each candidate must have rerank_score"
            assert isinstance(r["rerank_score"], float)

    @pytest.mark.asyncio
    async def test_rerank_score_is_float(self, reranker):
        """rerank_score must be a float."""
        candidates = _make_candidates(2)
        results = await reranker.rerank(
            query="hello",
            candidates=candidates,
            top_k=2,
        )
        for r in results:
            assert isinstance(r["rerank_score"], float)

    @pytest.mark.asyncio
    async def test_preserves_other_fields(self, reranker):
        """Reranker must preserve chunk_id and content_text from input."""
        candidates = _make_candidates(2)
        results = await reranker.rerank(
            query="hello",
            candidates=candidates,
            top_k=2,
        )
        for r in results:
            assert "chunk_id" in r
            assert "content_text" in r


# ---------------------------------------------------------------------------
# Respects top_k
# ---------------------------------------------------------------------------

class TestRespectsTopK:
    """Reranker must return at most top_k results."""

    @pytest.mark.asyncio
    async def test_returns_at_most_top_k(self, reranker):
        """When candidates > top_k, return only top_k."""
        candidates = _make_candidates(10)
        results = await reranker.rerank(
            query="topic 5",
            candidates=candidates,
            top_k=3,
        )
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_fewer_candidates_than_top_k(self, reranker):
        """When candidates < top_k, return all candidates."""
        candidates = _make_candidates(2)
        results = await reranker.rerank(
            query="topic 0",
            candidates=candidates,
            top_k=10,
        )
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_top_k_one(self, reranker):
        """top_k=1 should return at most 1 result."""
        candidates = _make_candidates(5)
        results = await reranker.rerank(
            query="topic 3",
            candidates=candidates,
            top_k=1,
        )
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Deterministic tie-break (FR-017)
# ---------------------------------------------------------------------------

class TestDeterministicTieBreak:
    """Ties must be broken deterministically: (rerank_score_desc, fused_score_desc, chunk_id_asc)."""

    @pytest.mark.asyncio
    async def test_deterministic_order(self, reranker):
        """Same input must produce same output order every time."""
        candidates = _make_candidates(5)
        results1 = await reranker.rerank(
            query="topic 0",
            candidates=list(candidates),
            top_k=5,
        )
        results2 = await reranker.rerank(
            query="topic 0",
            candidates=list(candidates),
            top_k=5,
        )
        assert [r["chunk_id"] for r in results1] == [r["chunk_id"] for r in results2]

    @pytest.mark.asyncio
    async def test_no_random_perturbation(self, reranker):
        """Running 5 times must produce identical ordering (no randomness)."""
        candidates = _make_candidates(8)
        first_order = None
        for _ in range(5):
            results = await reranker.rerank(
                query="topic 4",
                candidates=list(candidates),
                top_k=5,
            )
            order = [r["chunk_id"] for r in results]
            if first_order is None:
                first_order = order
            else:
                assert order == first_order, (
                    "Reranker must be deterministic — no random perturbation (FR-017)"
                )

    @pytest.mark.asyncio
    async def test_tie_broken_by_chunk_id(self, reranker):
        """When rerank_scores are equal, chunk_id ascending wins."""
        # Create candidates with identical text → identical rerank_score
        candidates = [
            {"chunk_id": "c_2", "content_text": "same text", "fused_score": 0.05},
            {"chunk_id": "c_1", "content_text": "same text", "fused_score": 0.05},
        ]
        results = await reranker.rerank(
            query="same",
            candidates=candidates,
            top_k=2,
        )
        # Both have the same rerank_score → tie broken by chunk_id ascending
        assert results[0]["chunk_id"] == "c_1", (
            "Tie must be broken by chunk_id ascending (FR-017)"
        )


# ---------------------------------------------------------------------------
# Candidate budget (FR-005)
# ---------------------------------------------------------------------------

class TestCandidateBudget:
    """Reranker only processes the candidates given to it (blueprint §18.5)."""

    @pytest.mark.asyncio
    async def test_only_processes_given_candidates(self, reranker):
        """Reranker must not fetch additional candidates beyond what's passed."""
        candidates = _make_candidates(3)
        results = await reranker.rerank(
            query="topic 0",
            candidates=candidates,
            top_k=5,
        )
        # Should return at most 3 (only what was given)
        assert len(results) <= 3
        returned_ids = {r["chunk_id"] for r in results}
        given_ids = {c["chunk_id"] for c in candidates}
        assert returned_ids.issubset(given_ids)

    @pytest.mark.asyncio
    async def test_empty_candidates(self, reranker):
        """Empty candidate list should return empty results."""
        results = await reranker.rerank(
            query="hello",
            candidates=[],
            top_k=5,
        )
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Results sorted by rerank_score descending
# ---------------------------------------------------------------------------

class TestResultOrdering:
    """Results must be sorted by rerank_score descending (with tie-break)."""

    @pytest.mark.asyncio
    async def test_sorted_by_rerank_score_desc(self, reranker):
        """Results must be sorted by rerank_score descending."""
        candidates = _make_candidates(5)
        results = await reranker.rerank(
            query="topic 0 topic 1 topic 2",
            candidates=candidates,
            top_k=5,
        )
        scores = [r["rerank_score"] for r in results]
        assert scores == sorted(scores, reverse=True), (
            "Results must be sorted by rerank_score descending"
        )
