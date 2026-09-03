"""Unit tests for the agentic retrieval pipeline helpers (T058 Red).

The state machine steps 4/5 MUST be wired to real retrieval:
  - planner graph signal maps to 004 expansion parameters (FR-033)
  - per-sub-problem recall candidates are merged across sub-problems with
    sub_problem_id traceability (FR-009)
  - per-source evidence cap default 3 / limit 5 is enforced (FR-006)
  - ledger scores are clamped to [0, 1] (evidence-ledger-entry.schema)

This test MUST FAIL before retrieval_pipeline.py exists (TDD Red).
"""

from __future__ import annotations

import pytest


class TestGraphParamMapping:
    """Planner signals -> 004 graph expansion params (FR-033)."""

    def test_graph_signal_selects_graph_path(self):
        from rag_mcp.orchestration.retrieval_pipeline import map_graph_params

        use_graph, relation_types = map_graph_params(
            signals=["dense", "sparse", "graph"],
            relation_directions=["calls", "called_by"],
        )
        assert use_graph is True
        assert set(relation_types) == {"calls", "called_by"}

    def test_no_graph_signal_disables_graph(self):
        from rag_mcp.orchestration.retrieval_pipeline import map_graph_params

        use_graph, relation_types = map_graph_params(
            signals=["dense", "sparse"],
            relation_directions=None,
        )
        assert use_graph is False
        assert relation_types is None

    def test_missing_directions_fall_back_to_004_bidirectional_default(self):
        from rag_mcp.orchestration.retrieval_pipeline import map_graph_params

        use_graph, relation_types = map_graph_params(
            signals=["graph"],
            relation_directions=None,
        )
        assert use_graph is True
        # 004 deterministic bidirectional default (FR-033)
        assert set(relation_types) == {
            "calls", "called_by", "fk_references", "fk_referenced_by",
        }

    def test_invalid_directions_fall_back_to_default(self):
        from rag_mcp.orchestration.retrieval_pipeline import map_graph_params

        use_graph, relation_types = map_graph_params(
            signals=["graph"],
            relation_directions=["bogus_direction"],
        )
        assert use_graph is True
        assert set(relation_types) == {
            "calls", "called_by", "fk_references", "fk_referenced_by",
        }


class TestMergeCandidates:
    """Cross-sub-problem candidate merge keeps traceability (FR-009)."""

    def _cand(self, chunk_id, sub, score=0.8, **kw):
        base = {
            "chunk_id": str(chunk_id),
            "sub_problem_id": sub,
            "retrieval_query": f"q{sub}",
            "retrievers": ["dense"],
            "score": score,
        }
        base.update(kw)
        return base

    def test_merge_dedupes_and_tracks_sub_problems(self):
        from rag_mcp.orchestration.retrieval_pipeline import merge_round_candidates

        per_sub = [
            [self._cand(1, 1), self._cand(2, 1)],
            [self._cand(2, 2), self._cand(3, 2)],
        ]
        merged = merge_round_candidates(per_sub)
        by_id = {c["evidence_id"]: c for c in merged}
        assert set(by_id) == {"1", "2", "3"}
        assert by_id["2"]["sub_problem_ids"] == [1, 2]
        assert by_id["1"]["sub_problem_ids"] == [1]

    def test_merge_keeps_best_score(self):
        from rag_mcp.orchestration.retrieval_pipeline import merge_round_candidates

        per_sub = [
            [self._cand(1, 1, score=0.4)],
            [self._cand(1, 2, score=0.9)],
        ]
        merged = merge_round_candidates(per_sub)
        assert len(merged) == 1
        assert merged[0]["score"] == pytest.approx(0.9)

    def test_merge_sorted_by_score_desc(self):
        from rag_mcp.orchestration.retrieval_pipeline import merge_round_candidates

        per_sub = [[self._cand(1, 1, score=0.2), self._cand(2, 1, score=0.7)]]
        merged = merge_round_candidates(per_sub)
        assert [c["evidence_id"] for c in merged] == ["2", "1"]


class TestPerSourceGuard:
    """Per-source evidence cap 3/limit 5 (FR-006)."""

    def _cand(self, chunk_id, source_id, score):
        return {
            "evidence_id": str(chunk_id),
            "source_id": source_id,
            "score": score,
        }

    def test_default_cap_3_per_source(self):
        from rag_mcp.orchestration.retrieval_pipeline import apply_per_source_guard

        cands = [self._cand(i, "s1", 0.9 - i * 0.01) for i in range(5)]
        kept = apply_per_source_guard(cands, max_per_source=3)
        assert len(kept) == 3
        assert [c["evidence_id"] for c in kept] == ["0", "1", "2"]

    def test_cap_limit_5_max(self):
        from rag_mcp.orchestration.retrieval_pipeline import apply_per_source_guard

        cands = [self._cand(i, "s1", 0.9 - i * 0.01) for i in range(8)]
        kept = apply_per_source_guard(cands, max_per_source=5)
        assert len(kept) == 5

    def test_diversity_across_sources_preserved(self):
        from rag_mcp.orchestration.retrieval_pipeline import apply_per_source_guard

        cands = (
            [self._cand(i, "s1", 0.9 - i * 0.01) for i in range(4)]
            + [self._cand(100, "s2", 0.5)]
        )
        kept = apply_per_source_guard(cands, max_per_source=3)
        sources = {c["source_id"] for c in kept}
        assert "s2" in sources


class TestScoreClamp:
    """Ledger score must be in [0, 1] (schema chk_ledger_score)."""

    def test_scores_clamped(self):
        from rag_mcp.orchestration.retrieval_pipeline import clamp01

        assert clamp01(1.4) == 1.0
        assert clamp01(-0.2) == 0.0
        assert clamp01(0.62) == pytest.approx(0.62)

    def test_rrf_fused_score_normalized(self):
        from rag_mcp.orchestration.retrieval_pipeline import final_candidate_score

        # RRF fused scores are small (~1/(60+rank)); they must normalize into
        # [0, 1] when no dense/sparse/rerank score is available.
        score = final_candidate_score(
            dense_score=None, sparse_score=None, rerank_score=None,
            fused_score=0.0328, structure_weight=None,
        )
        assert 0.0 <= score <= 1.0
        assert score > 0

    def test_rerank_score_preferred(self):
        from rag_mcp.orchestration.retrieval_pipeline import final_candidate_score

        score = final_candidate_score(
            dense_score=0.4, sparse_score=0.3, rerank_score=0.87,
            fused_score=0.03, structure_weight=None,
        )
        assert score == pytest.approx(0.87)

    def test_fused_score_preferred_over_direct_scores(self):
        """T069 (SC-002/SC-015): agentic candidate ordering must match the
        deterministic baseline ordering.

        Without a reranker the baseline orders evidence by the RRF fused
        score; the agentic merge must use the SAME criterion so that a
        single-sub-problem (original-query) run reproduces the baseline
        ranking exactly. max(dense, sparse) ordering systematically
        promotes broad class-level chunks over the precise multi-path
        agreed chunk the baseline ranks first.
        """
        from rag_mcp.orchestration.retrieval_pipeline import (
            _RRF_SCALE,
            final_candidate_score,
        )

        score = final_candidate_score(
            dense_score=0.9, sparse_score=None, rerank_score=None,
            fused_score=0.03, structure_weight=None,
        )
        assert score == pytest.approx(0.03 * _RRF_SCALE), (
            "fused score must drive the ordering when rerank is absent"
        )
        assert score < 0.9, "a dense-only score must not outrank fused ordering"

    def test_fused_ordering_reproduces_baseline_rank_sequence(self):
        """Candidates ranked by fused score keep the baseline order (T069)."""
        from rag_mcp.orchestration.retrieval_pipeline import (
            final_candidate_score,
            merge_round_candidates,
        )

        # Simulate a hybrid recall list ordered by fused score: the
        # multi-path agreed chunk (0.0328) beats the dense-only chunk
        # (0.0164) even though the dense-only chunk has a higher raw
        # dense score (the q2/q3/q4 regression pattern).
        group = [
            {"evidence_id": "A", "score": final_candidate_score(
                0.71, None, None, 0.0328, None)},
            {"evidence_id": "B", "score": final_candidate_score(
                0.92, None, None, 0.0164, None)},
            {"evidence_id": "C", "score": final_candidate_score(
                0.65, None, None, 0.0125, None)},
        ]
        merged = merge_round_candidates([group])
        assert [c["evidence_id"] for c in merged] == ["A", "B", "C"], (
            "merged order must follow the fused (baseline) ordering"
        )

    def test_merge_tie_break_mirrors_baseline_rrf(self):
        """T069: when fused scores tie, the merge MUST mirror the baseline
        RRF tie-break (dense_rank asc, sparse_rank asc, chunk_id asc), NOT
        chunk_id alone. chunk_id-asc-only flips dense-rank-1 (the precise
        method chunk) behind dense-rank-2 (the broad class chunk) — the
        systematic q2/q11/q30/...816-first regression pattern."""
        from rag_mcp.orchestration.retrieval_pipeline import merge_round_candidates

        # Both candidates tie on fused score; B has a higher raw DENSE
        # score (=> lower dense rank) so the baseline ranks it first even
        # though A's numeric id sorts first.
        a = {
            "evidence_id": "100",  # smaller id
            "score": 0.492,
            "dense_score": 0.71, "sparse_score": 0.88,
        }
        b = {
            "evidence_id": "200",  # larger id
            "score": 0.492,
            "dense_score": 0.95, "sparse_score": 0.60,
        }
        merged = merge_round_candidates([[a, b]])
        assert [c["evidence_id"] for c in merged] == ["200", "100"], (
            "dense_score (== dense_rank within one list) must break "
            "fused-score ties before chunk_id, matching the baseline"
        )
