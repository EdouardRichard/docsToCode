"""Unit test for LangGraph state machine skeleton (T012 Red).

Tests the deterministic state machine that drives the nine-step main flow
with bounded supplementary retrieval loop and guardrails:
  - Nine-step main state flow order (FR-004, blueprint sec 12)
  - Guardrails enforced: rounds/timeout/binning (FR-006)
  - State isolated by request_id/run_id (FR-025, blueprint sec 21.1)
  - No global active project (Constitution I, FR-025)
  - Deterministic controller owns jumps (Constitution VI)

This test MUST FAIL before state_machine.py is implemented (TDD Red).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


STEPS = [
    "receive_validate",      # Step 1: Receive and validate request
    "resolve_scope",          # Step 2: Resolve knowledge scope
    "query_planning",         # Step 3: Query planning (decompose sub_problems)
    "parallel_retrieval",     # Step 4: Parallel retrieval (Dense/Sparse/graph)
    "fusion_rerank",          # Step 5: Fusion + Rerank
    "evidence_analysis",      # Step 6: Evidence analysis (coverage/conflict/gap)
    "loop_decision",          # Step 7: Supplementary loop decision
    "context_orchestration",  # Step 8: Context orchestration (dedup/diversity/binning)
    "response_serialization", # Step 9: MCP response serialization
]


class TestStateMachineImport:
    def test_import_state_machine(self):
        """AgenticStateMachine must be importable."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        assert AgenticStateMachine is not None


class TestNineStepFlow:
    """FR-004: nine-step main state flow order (blueprint sec 12)."""

    def _make_machine(self):
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        machine = AgenticStateMachine(
            run_id="999",
            request_id="req-1",
            project_scope=["proj-a"],
            knowledge_scope_ids=["100"],
        )
        return machine

    def test_nine_steps_defined(self):
        """The state machine must define exactly nine steps."""
        machine = self._make_machine()
        steps = machine.get_step_names()
        assert len(steps) == 9, f"Expected 9 steps, got {len(steps)}"

    def test_step_order_correct(self):
        """Steps must be in the correct order (blueprint sec 12)."""
        machine = self._make_machine()
        steps = machine.get_step_names()
        expected = [
            "receive_validate",
            "resolve_scope",
            "query_planning",
            "parallel_retrieval",
            "fusion_rerank",
            "evidence_analysis",
            "loop_decision",
            "context_orchestration",
            "response_serialization",
        ]
        assert steps == expected, f"Step order mismatch: {steps}"

    def test_run_records_step_order(self):
        """run() must execute steps in the defined order."""
        machine = self._make_machine()
        machine.run(context={"query": "test query"})
        executed = machine.get_executed_steps()
        assert len(executed) >= 9, f"Expected >=9 steps, got {len(executed)}"
        # First nine should be the main flow
        assert executed[0] == "receive_validate"
        assert executed[1] == "resolve_scope"
        assert executed[-1] == "response_serialization"


class TestGuardrailsEnforced:
    """FR-006: guardrails enforced (rounds/timeout/binning)."""

    def _make_machine(self, max_rounds=2):
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        machine = AgenticStateMachine(
            run_id="999",
            request_id="req-1",
            project_scope=["proj-a"],
            knowledge_scope_ids=["100"],
            max_rounds=max_rounds,
        )
        return machine

    def test_max_rounds_guardrail(self):
        """rounds_completed must not exceed max_rounds (FR-006)."""
        machine = self._make_machine(max_rounds=2)
        machine.run(context={"query": "test", "force_gap": True})
        assert machine.rounds_completed <= machine.max_rounds

    def test_top_k_guardrail(self):
        """top_k must not exceed 20 (FR-006 binning cap)."""
        machine = self._make_machine()
        assert machine.top_k_max <= 20

    def test_total_timeout_guardrail(self):
        """total_timeout_ms must be <= 30000 (blueprint sec 19)."""
        machine = self._make_machine()
        assert machine.total_timeout_ms <= 30000

    def test_node_timeout_guardrail(self):
        """node_timeout_ms must be <= 10000 (blueprint sec 19)."""
        machine = self._make_machine()
        assert machine.node_timeout_ms <= 10000


class TestStateIsolation:
    """FR-025: state isolated by request_id/run_id (blueprint sec 21.1)."""

    def test_run_id_is_unique_per_instance(self):
        """Each state machine instance has its own run_id."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        m1 = AgenticStateMachine(
            run_id="111", request_id="r1",
            project_scope=["a"], knowledge_scope_ids=["1"],
        )
        m2 = AgenticStateMachine(
            run_id="222", request_id="r2",
            project_scope=["b"], knowledge_scope_ids=["2"],
        )
        assert m1.run_id != m2.run_id
        assert m1.request_id != m2.request_id

    def test_no_global_active_project(self):
        """No global active project state (Constitution I, FR-025)."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        m1 = AgenticStateMachine(
            run_id="111", request_id="r1",
            project_scope=["a"], knowledge_scope_ids=["1"],
        )
        m2 = AgenticStateMachine(
            run_id="222", request_id="r2",
            project_scope=["b"], knowledge_scope_ids=["2"],
        )
        # m1 state should not affect m2
        m1.run(context={"query": "test1"})
        # m2 should start fresh, not see m1 state
        assert m2.rounds_completed == 0
        assert m2.get_executed_steps() == []

    def test_project_scope_isolated(self):
        """Each instance carries its own project_scope (no shared state)."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        m1 = AgenticStateMachine(
            run_id="111", request_id="r1",
            project_scope=["proj-a"], knowledge_scope_ids=["1"],
        )
        m2 = AgenticStateMachine(
            run_id="222", request_id="r2",
            project_scope=["proj-b"], knowledge_scope_ids=["2"],
        )
        assert m1.project_scope == ["proj-a"]
        assert m2.project_scope == ["proj-b"]
        assert m1.project_scope != m2.project_scope


class TestDeterministicController:
    """Constitution VI: deterministic controller owns jumps, not Agent."""

    def _make_machine(self, max_rounds=2):
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        return AgenticStateMachine(
            run_id="999", request_id="req-1",
            project_scope=["proj-a"], knowledge_scope_ids=["100"],
            max_rounds=max_rounds,
        )

    def test_loop_decision_is_deterministic(self):
        """Loop decision should be made by the controller, not the Agent."""
        machine = self._make_machine(max_rounds=2)
        # When force_gap=True, the controller decides whether to continue
        machine.run(context={"query": "test", "force_gap": True})
        # The controller should have made the decision (recorded)
        assert hasattr(machine, "controller_decisions")
        assert len(machine.controller_decisions) > 0

    def test_no_gap_no_loop(self):
        """When there is no gap, the controller should not loop (FR-005)."""
        machine = self._make_machine(max_rounds=2)
        machine.run(context={"query": "test", "force_gap": False})
        assert machine.rounds_completed == 1  # Only first round, no supplementary

    def test_gap_triggers_loop_within_limit(self):
        """Gap triggers supplementary loop but within max_rounds (FR-005/FR-006)."""
        machine = self._make_machine(max_rounds=2)
        machine.run(context={"query": "test", "force_gap": True})
        assert machine.rounds_completed <= 2
        assert machine.rounds_completed >= 1

    def test_max_rounds_stops_loop(self):
        """Loop must stop at max_rounds even if gap persists (FR-006)."""
        machine = self._make_machine(max_rounds=1)
        machine.run(context={"query": "test", "force_gap": True})
        assert machine.rounds_completed == 1  # Cannot exceed max_rounds=1


class TestCompletionStatus:
    """Blueprint sec 14: four-state completion status."""

    def _make_machine(self):
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        return AgenticStateMachine(
            run_id="999", request_id="req-1",
            project_scope=["proj-a"], knowledge_scope_ids=["100"],
        )

    def test_completion_status_is_four_state(self):
        """After run, completion_status must be one of the four states."""
        machine = self._make_machine()
        machine.run(context={"query": "test"})
        assert machine.completion_status in ("complete", "partial", "no_evidence", "failed")

    def test_state_envelope_integration(self):
        """The state machine should produce a StateEnvelope with the run record."""
        machine = self._make_machine()
        machine.run(context={"query": "test"})
        envelope = machine.get_state_envelope()
        assert envelope is not None
        record = envelope.to_dict()
        assert record["run_id"] == "999"
        assert record["request_id"] == "req-1"
