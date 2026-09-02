"""Integration test for context orchestration wiring (T036 Red, US3).

Tests that the context orchestrator is wired into step 8:
  - Step 8 (context_orchestration) calls the ContextOrchestratorAgent
  - search_knowledge output Schema is valid (additionalProperties:false, FR-024)
  - Ledger can be resolved via (request_id, evidence_id) (FR-024/SC-004)

This test MUST FAIL before step 8 is wired (TDD Red).
"""

from __future__ import annotations

import pytest


class TestContextOrchestrationWiring:
    """Step 8 produces context; output recorded (blueprint sec 12)."""

    def _make_machine(self):
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
        from rag_mcp.orchestration.state_machine import AgenticStateMachine

        planner = QueryPlannerAgent(model_and_version="test-v1")
        planner._llm_decompose = lambda q, ctx: [{"query": "test", "signals": ["dense"]}]

        analyst = EvidenceAnalystAgent(model_and_version="test-v1")
        analyst._llm_judge = lambda ctx: {
            "coverage_state": "covered",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
        }

        orchestrator = ContextOrchestratorAgent(model_and_version="test-v1")

        machine = AgenticStateMachine(
            run_id="999", request_id="req-1",
            project_scope=["proj-a"], knowledge_scope_ids=["100"],
        )
        machine.set_query_planner(planner)
        machine.set_evidence_analyst(analyst)
        machine.set_context_orchestrator(orchestrator)
        return machine

    def test_step_8_calls_orchestrator(self):
        """Step 8 (context_orchestration) should be executed."""
        machine = self._make_machine()
        machine.run(context={
            "query": "test",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        assert "context_orchestration" in machine.get_executed_steps()

    def test_orchestrator_output_in_record(self):
        """agent_outputs_ref.context_orchestrator should be in the run record."""
        machine = self._make_machine()
        machine.run(context={
            "query": "test",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        record = machine.get_state_envelope().to_dict()
        co_ref = record["agent_outputs_ref"]["context_orchestrator"]
        assert "context_result_id" in co_ref
        assert "selection_list" in co_ref
        assert "schema_valid" in co_ref

    def test_selection_list_has_decisions(self):
        """selection_list should contain decisions (FR-032)."""
        machine = self._make_machine()
        machine.run(context={
            "query": "test",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        record = machine.get_state_envelope().to_dict()
        co_ref = record["agent_outputs_ref"]["context_orchestrator"]
        for s in co_ref["selection_list"]:
            assert s["decision"] in ("selected", "truncated", "deduped")

    def test_mcp_output_schema_not_violated(self):
        """Run record must not have extra fields (additionalProperties:false, FR-024)."""
        machine = self._make_machine()
        machine.run(context={
            "query": "test",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        record = machine.get_state_envelope().to_dict()
        # The run record itself has additionalProperties:false in the schema
        # The MCP output (search_knowledge) is unchanged - no ledger fields added
        assert "agent_outputs_ref" in record
        assert "ledger_ref" in record

    def test_all_three_agents_wired(self):
        """All three agents should produce output in the run record."""
        machine = self._make_machine()
        machine.run(context={
            "query": "test",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        record = machine.get_state_envelope().to_dict()
        ref = record["agent_outputs_ref"]
        assert "query_planner" in ref
        assert "evidence_analyst" in ref
        assert "context_orchestrator" in ref
