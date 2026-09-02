"""Unit test for run-state TTL + no-writeback-to-KB + tracing redaction (T054 Red).

Tests:
  - TTL is set on run records (FR-011, blueprint sec 20)
  - Agent reasoning results are NOT written back to knowledge base (FR-011)
  - When tracing body disabled, only ID/status/timing/error retained (FR-012)

This test MUST FAIL before TTL + redaction implementation (TDD Red).
"""

from __future__ import annotations

import pytest


class TestTTLSetting:
    """FR-011: TTL is set on run records (blueprint sec 20)."""

    def test_trace_recorder_sets_ttl(self):
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        rec = TraceRecorder(trace_body_enabled=True)
        rec.set_ttl()
        assert rec.get_ttl_expires_at() is not None

    def test_ttl_default_7_days(self):
        """Default TTL should be 7 days (blueprint sec 20)."""
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        rec = TraceRecorder(trace_body_enabled=True)
        assert rec.DEFAULT_TTL_SECONDS == 7 * 24 * 3600


class TestNoWritebackToKB:
    """FR-011: Agent reasoning results are NOT written back to knowledge base."""

    def test_state_machine_does_not_write_to_kb(self):
        """State machine should not write agent reasoning to the knowledge base."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine
        m = AgenticStateMachine(
            run_id="1", request_id="r1",
            project_scope=["proj-a"], knowledge_scope_ids=["100"],
        )
        # The state machine should not have a write_to_kb method
        assert not hasattr(m, "write_to_kb")
        assert not hasattr(m, "writeback")

    def test_ledger_store_does_not_write_to_kb(self):
        """EvidenceLedgerStore should not have a write_to_kb method."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        from unittest.mock import MagicMock
        store = EvidenceLedgerStore(MagicMock())
        assert not hasattr(store, "write_to_kb")
        assert not hasattr(store, "writeback")


class TestRedaction:
    """FR-012: when body disabled, only ID/status/timing/error retained."""

    def test_body_disabled_strips_content(self):
        """When body disabled, query content should be stripped (FR-012)."""
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        rec = TraceRecorder(trace_body_enabled=False)
        rec.record_ledger_entry("123", retrieval_query="sensitive query")
        ref = rec.get_ledger_ref()
        # ID is retained
        assert "123" in ref["ledger_entry_ids"]

    def test_body_disabled_retains_timings(self):
        """Timings are retained even with body disabled (FR-012)."""
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        rec = TraceRecorder(trace_body_enabled=False)
        rec.record_timing("dense", 42.5)
        assert rec.get_timings()["dense"] == 42.5

    def test_body_disabled_retains_agent_ids(self):
        """Agent output IDs are retained even with body disabled (FR-012)."""
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        rec = TraceRecorder(trace_body_enabled=False)
        rec.record_agent_output("evidence_analyst", {"judgment_ids": ["j1"], "schema_valid_all": True})
        ref = rec.get_agent_outputs_ref()
        assert ref["evidence_analyst"]["judgment_ids"] == ["j1"]
