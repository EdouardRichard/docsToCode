"""Tests for the slim analyst evidence view (T069, SC-007).

The evidence-analyst LLM prompt must stay bounded so node latency stays
within the guardrails (SC-007, fewer 30s degradations): the evidence
view sent to the analyst caps the number of items and the excerpt
length. Candidates are already score-sorted by the merge, so the cap
keeps the most relevant head (which is what coverage/gap judgment
actually needs).

This test MUST FAIL before the caps exist (TDD Red).
"""

from __future__ import annotations

from rag_mcp.orchestration.state_machine import AgenticStateMachine


def _machine() -> AgenticStateMachine:
    return AgenticStateMachine(
        run_id="1", request_id="req-1",
        project_scope=["p"], knowledge_scope_ids=["100"],
    )


def _cand(i: int, excerpt: str) -> dict:
    return {
        "evidence_id": str(i),
        "content_excerpt": excerpt,
        "score": 1.0 - i * 0.001,
        "source_position": f"path#{i}",
        "sub_problem_ids": [1],
    }


class TestEvidenceViewSlim:
    def test_items_capped(self):
        machine = _machine()
        cands = [_cand(i, "chunk " + "x" * 50) for i in range(60)]
        view = machine._build_evidence_view(cands)
        assert len(view) <= 30, "analyst evidence view must be capped"

    def test_excerpt_capped(self):
        machine = _machine()
        long_excerpt = "y" * 5000
        cands = [_cand(1, long_excerpt)]
        view = machine._build_evidence_view(cands)
        assert len(view) == 1
        assert len(view[0]["content_excerpt"]) <= 160, (
            "excerpts sent to the analyst must be truncated"
        )

    def test_top_scoring_items_kept(self):
        machine = _machine()
        # 40 candidates; only the highest-scoring head is kept
        cands = [_cand(i, f"chunk-{i}") for i in range(40)]
        view = machine._build_evidence_view(cands)
        kept_ids = {v["evidence_id"] for v in view}
        assert "0" in kept_ids and "1" in kept_ids
        assert "39" not in kept_ids, "tail candidates must be dropped"

    def test_empty_view(self):
        machine = _machine()
        assert machine._build_evidence_view([]) == []
