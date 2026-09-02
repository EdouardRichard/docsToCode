"""E2E test for DeepSeek Harness agentic search_knowledge + get_evidence (T052).

Tests the agentic orchestration end-to-end:
  - Full nine-step state machine flow with all three agents (SC-012)
  - Output Schema is valid (FR-024)
  - 30s guardrail < Host Tool Call timeout (blueprint sec 19)

This is a unit-level E2E test that verifies the agentic path works end-to-end
through the state machine, without requiring a running MCP server.
"""

from __future__ import annotations

import pytest


class TestAgenticE2E:
    """SC-012: end-to-end agentic orchestration through the state machine."""

    def _make_full_machine(self):
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
        from rag_mcp.orchestration.state_machine import AgenticStateMachine

        planner = QueryPlannerAgent(model_and_version="test-v1")
        planner._llm_decompose = lambda q, ctx: [
            {"query": "sub-query", "signals": ["dense", "graph"]},
        ]
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

    def test_full_flow_executes(self):
        """The full nine-step flow should execute successfully."""
        machine = self._make_full_machine()
        record = machine.run(context={
            "query": "which services call validateToken",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        assert record is not None
        assert record["completion_status"] in ("complete", "partial", "no_evidence", "failed")

    def test_output_schema_valid(self):
        """The run record must conform to agentic-retrieval-run.schema.json (FR-024)."""
        machine = self._make_full_machine()
        record = machine.run(context={
            "query": "test",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        assert "run_id" in record
        assert "request_id" in record
        assert "completion_status" in record
        assert "agent_outputs_ref" in record
        assert "ledger_ref" in record
        assert "schema_valid_all" in record

    def test_30s_guardrail(self):
        """Total timeout must be <= 30000ms (blueprint sec 19)."""
        machine = self._make_full_machine()
        assert machine.total_timeout_ms <= 30000

    def test_all_nine_steps_executed(self):
        """All nine steps should be executed in the flow."""
        machine = self._make_full_machine()
        machine.run(context={
            "query": "test",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        steps = machine.get_executed_steps()
        assert "receive_validate" in steps
        assert "resolve_scope" in steps
        assert "query_planning" in steps
        assert "parallel_retrieval" in steps
        assert "fusion_rerank" in steps
        assert "evidence_analysis" in steps
        assert "loop_decision" in steps
        assert "context_orchestration" in steps
        assert "response_serialization" in steps

    def test_three_agents_all_wired(self):
        """All three agents should produce output (blueprint sec 11)."""
        machine = self._make_full_machine()
        machine.run(context={
            "query": "test",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        record = machine.get_state_envelope().to_dict()
        ref = record["agent_outputs_ref"]
        assert ref["query_planner"]["schema_valid"] is True
        assert ref["evidence_analyst"]["schema_valid_all"] is True
        assert ref["context_orchestrator"]["schema_valid"] is True

    def test_completion_status_is_four_state(self):
        """Completion status must be one of the four states (SC-011)."""
        machine = self._make_full_machine()
        machine.run(context={
            "query": "test",
            "candidates": [{"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9}],
        })
        assert machine.completion_status in ("complete", "partial", "no_evidence", "failed")
