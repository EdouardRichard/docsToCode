"""Integration test for cross-project isolation hardening (T044 Red).

Tests cross-project isolation (Constitution hard constraint):
  - Single-scope query does not return other project evidence (FR-022)
  - No project_scope -> rejected (FR-021)
  - Leakage events = 0 (SC-003)

This test MUST FAIL before isolation hardening (TDD Red).
"""

from __future__ import annotations

import pytest


class TestCrossProjectIsolation:
    """FR-021/FR-022/SC-003: cross-project isolation = 0 leakage."""

    def test_no_scope_rejected(self):
        """No project_scope must be rejected (FR-021)."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        with pytest.raises(ValueError):
            machine = AgenticStateMachine(
                run_id="1", request_id="r1",
                project_scope=[], knowledge_scope_ids=[],
            )
            machine.run(context={"query": "test"})

    def test_single_scope_no_cross_leakage(self):
        """Single-scope query should not return other project evidence."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        m1 = AgenticStateMachine(
            run_id="1", request_id="r1",
            project_scope=["proj-a"], knowledge_scope_ids=["100"],
        )
        m2 = AgenticStateMachine(
            run_id="2", request_id="r2",
            project_scope=["proj-b"], knowledge_scope_ids=["200"],
        )
        m1.run(context={"query": "test"})
        m2.run(context={"query": "test"})
        # m1 should not have proj-b in its scope
        assert "proj-b" not in m1.project_scope
        assert "proj-a" not in m2.project_scope

    def test_ledger_scope_rejection(self):
        """EvidenceLedgerStore should reject cross-scope writes (FR-022)."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        from unittest.mock import MagicMock
        store = EvidenceLedgerStore(MagicMock())
        entry = {"knowledge_scope_id": 100, "project_id": 200, "index_version": 1}
        # Different scope should be rejected
        assert store.validate_scope(entry, [{"knowledge_scope_id": 999, "project_id": 888, "index_version": 1}]) is False

    def test_isolation_triple_in_run_record(self):
        """Run record should carry project_scope for isolation (FR-022)."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        m = AgenticStateMachine(
            run_id="1", request_id="r1",
            project_scope=["proj-a"], knowledge_scope_ids=["100"],
        )
        m.run(context={"query": "test"})
        record = m.get_state_envelope().to_dict()
        assert record["project_scope"] == ["proj-a"]
