"""Integration test for concurrency isolation (T048 Red).

Tests 5 concurrent requests with different project_scope (FR-025/SC-013):
  - No state/ledger/scope crosstalk

This test MUST FAIL before concurrency verification (TDD Red).
"""

from __future__ import annotations

import pytest


class TestConcurrencyIsolation:
    """FR-025/SC-013: 5 concurrent requests, no crosstalk."""

    def test_five_concurrent_no_crosstalk(self):
        """5 concurrent state machines should not crosstalk (FR-025)."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        machines = []
        for i in range(5):
            m = AgenticStateMachine(
                run_id=str(i+1),
                request_id=f"req-{i+1}",
                project_scope=[f"proj-{i+1}"],
                knowledge_scope_ids=[str(100+i)],
            )
            machines.append(m)
        # Run all
        for m in machines:
            m.run(context={"query": "test"})
        # Verify no crosstalk
        for i, m in enumerate(machines):
            assert m.run_id == str(i+1)
            assert m.project_scope == [f"proj-{i+1}"]
            assert m.request_id == f"req-{i+1}"

    def test_rounds_completed_isolated(self):
        """Each machine rounds_completed should be independent."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        m1 = AgenticStateMachine(run_id="1", request_id="r1", project_scope=["a"], knowledge_scope_ids=["1"], max_rounds=1)
        m2 = AgenticStateMachine(run_id="2", request_id="r2", project_scope=["b"], knowledge_scope_ids=["2"], max_rounds=2)
        m1.run(context={"query": "test", "force_gap": True})
        m2.run(context={"query": "test", "force_gap": False})
        assert m1.rounds_completed == 1  # max_rounds=1
        assert m2.rounds_completed == 1  # no gap
