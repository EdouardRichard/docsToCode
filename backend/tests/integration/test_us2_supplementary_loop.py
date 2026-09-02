"""Integration test for supplementary loop (T028 Red, US2).

Tests the bounded supplementary retrieval loop (6->3->4->5->6->7):
  - rounds_completed <= max_rounds (2, FR-006)
  - Supplementary candidates re-enter fusion/rerank/analysis (FR-014)
  - Reaching max_rounds with gaps -> partial with gaps (FR-016)
  - Deterministic controller (not Agent) decides to continue (Constitution VI)

This test MUST FAIL before the loop is implemented (TDD Red).
"""

from __future__ import annotations

import pytest


class TestSupplementaryLoop:
    """FR-005/FR-014: bounded supplementary retrieval loop."""

    def _make_machine_with_agents(self, max_rounds=2, needs_supplementary=True):
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        from rag_mcp.orchestration.state_machine import AgenticStateMachine

        planner = QueryPlannerAgent(model_and_version="test-v1")
        planner._llm_decompose = lambda query, ctx: [
            {"query": "sub-query", "signals": ["dense"]},
        ]

        analyst = EvidenceAnalystAgent(model_and_version="test-v1")
        analyst._llm_judge = lambda ctx: {
            "coverage_state": "partial" if needs_supplementary else "covered",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [1] if needs_supplementary else [],
            "needs_supplementary": needs_supplementary,
            "gap_descriptions": [{"description": "gap"}] if needs_supplementary else [],
        }

        machine = AgenticStateMachine(
            run_id="999",
            request_id="req-1",
            project_scope=["proj-a"],
            knowledge_scope_ids=["100"],
            max_rounds=max_rounds,
        )
        machine.set_query_planner(planner)
        machine.set_evidence_analyst(analyst)
        return machine

    def test_rounds_within_limit(self):
        """rounds_completed must not exceed max_rounds (FR-006)."""
        machine = self._make_machine_with_agents(max_rounds=2, needs_supplementary=True)
        machine.run(context={"query": "test"})
        assert machine.rounds_completed <= 2

    def test_gap_triggers_loop(self):
        """Gap should trigger supplementary loop (FR-005)."""
        machine = self._make_machine_with_agents(max_rounds=2, needs_supplementary=True)
        machine.run(context={"query": "test"})
        assert machine.rounds_completed >= 2  # at least 2 rounds when gap persists

    def test_no_gap_no_loop(self):
        """No gap should not trigger loop (FR-005)."""
        machine = self._make_machine_with_agents(max_rounds=2, needs_supplementary=False)
        machine.run(context={"query": "test"})
        assert machine.rounds_completed == 1

    def test_controller_decides_not_agent(self):
        """The deterministic controller (not Agent) decides to continue (Constitution VI)."""
        machine = self._make_machine_with_agents(max_rounds=2, needs_supplementary=True)
        machine.run(context={"query": "test"})
        # Controller decisions should be recorded
        assert len(machine.controller_decisions) > 0
        for decision in machine.controller_decisions:
            assert "should_continue" in decision
            assert "reason" in decision

    def test_max_rounds_partial_with_gaps(self):
        """Reaching max_rounds with gaps -> partial (FR-016)."""
        machine = self._make_machine_with_agents(max_rounds=2, needs_supplementary=True)
        machine.run(context={"query": "test"})
        assert machine.completion_status == "partial"

    def test_no_gap_completes(self):
        """No gap -> complete status (blueprint sec 14)."""
        machine = self._make_machine_with_agents(max_rounds=2, needs_supplementary=False)
        machine.run(context={"query": "test"})
        assert machine.completion_status == "complete"

    def test_evidence_analyst_output_in_record(self):
        """Evidence analyst judgment should be in agent_outputs_ref (FR-031)."""
        machine = self._make_machine_with_agents(max_rounds=2, needs_supplementary=False)
        machine.run(context={"query": "test"})
        record = machine.get_state_envelope().to_dict()
        ea_ref = record["agent_outputs_ref"]["evidence_analyst"]
        assert "judgment_ids" in ea_ref
        assert "schema_valid_all" in ea_ref

    def test_max_rounds_one_no_loop(self):
        """max_rounds=1 means no supplementary loop possible (FR-006)."""
        machine = self._make_machine_with_agents(max_rounds=1, needs_supplementary=True)
        machine.run(context={"query": "test"})
        assert machine.rounds_completed == 1
        assert machine.completion_status == "partial"
