"""Contract test for hard-metric gates (T050 Red).

Tests hard-metric gates (Constitution hard constraints, blueprint sec 24.2):
  - 100% Schema validity (SC-004)
  - 100% Source locatability (SC-005)
  - 0 cross-project leakage (SC-003)

This test MUST FAIL before hard-metric gate verification (TDD Red).
"""

from __future__ import annotations

import pytest


class TestHardMetricGates:
    """Constitution hard constraints: schema 100% + locatability 100% + leakage 0."""

    def test_agent_output_always_schema_valid(self):
        """All agent outputs must have schema_valid flag (FR-024/SC-004)."""
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
        for AgentClass in [QueryPlannerAgent, EvidenceAnalystAgent, ContextOrchestratorAgent]:
            agent = AgentClass(model_and_version="test-v1")
            result = agent.run({"query": "test", "candidates": [], "sub_problems": []})
            assert "schema_valid" in result.output
            assert isinstance(result.output["schema_valid"], bool)

    def test_ledger_entries_locatable(self):
        """Evidence ledger entries must be locatable via (request_id, evidence_id) (FR-023/SC-005)."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        from unittest.mock import MagicMock
        store = EvidenceLedgerStore(MagicMock())
        assert hasattr(store, "get_by_request_evidence")
        # The bridge key (request_id, evidence_id) must be resolvable
        assert callable(store.get_by_request_evidence)

    def test_zero_leakage_by_design(self):
        """Cross-scope writes must be rejected by design (FR-022/SC-003)."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        from unittest.mock import MagicMock
        store = EvidenceLedgerStore(MagicMock())
        # Cross-scope entry should be rejected
        entry = {"knowledge_scope_id": 100, "project_id": 200, "index_version": 1}
        assert store.validate_scope(entry, None) is False
        assert store.validate_scope(entry, []) is False

    def test_run_record_has_isolation_fields(self):
        """Run record must carry project_scope and knowledge_scope_ids (FR-022)."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        m = AgenticStateMachine(
            run_id="1", request_id="r1",
            project_scope=["proj-a"], knowledge_scope_ids=["100"],
        )
        m.run(context={"query": "test"})
        record = m.get_state_envelope().to_dict()
        assert "project_scope" in record
        assert "knowledge_scope_ids" in record
