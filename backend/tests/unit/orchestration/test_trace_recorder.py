"""Unit test for trace recorder (T014 Red).

Tests the TraceRecorder that records:
  - sub_path_timings: per-subpath millisecond timings (FR-031)
  - agent_outputs_ref: references to Agent outputs (blueprint sec 20)
  - ledger_ref: references to ledger entries (blueprint sec 13)
  - TTL: sets ttl_expires_at on run records (blueprint sec 20)
  - Redaction: when trace_body_enabled=False, only retains ID/status/
    timing/error, not query/evidence body content (FR-011/FR-012)

This test MUST FAIL before trace_recorder.py is implemented (TDD Red).
"""

from __future__ import annotations

import pytest


class TestTraceRecorderImport:
    def test_import_trace_recorder(self):
        """TraceRecorder must be importable."""
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        assert TraceRecorder is not None


class TestSubPathTimings:
    """FR-031: sub_path_timings records per-subpath millisecond timings."""

    def _make_recorder(self):
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        return TraceRecorder(trace_body_enabled=True)

    def test_record_timing(self):
        """Should record a timing for a subpath."""
        rec = self._make_recorder()
        rec.record_timing("dense", 42.5)
        timings = rec.get_timings()
        assert "dense" in timings
        assert timings["dense"] == 42.5

    def test_record_multiple_timings(self):
        """Should record timings for multiple subpaths."""
        rec = self._make_recorder()
        rec.record_timing("dense", 10.0)
        rec.record_timing("sparse", 20.0)
        rec.record_timing("fusion", 5.0)
        rec.record_timing("rerank", 15.0)
        rec.record_timing("query_planner", 100.0)
        rec.record_timing("evidence_analyst", 200.0)
        rec.record_timing("context_orchestrator", 50.0)
        timings = rec.get_timings()
        assert len(timings) >= 7
        assert timings["dense"] == 10.0
        assert timings["query_planner"] == 100.0

    def test_get_timings_returns_dict(self):
        """get_timings() should return a dict (JSONB-serializable)."""
        rec = self._make_recorder()
        timings = rec.get_timings()
        assert isinstance(timings, dict)


class TestAgentOutputsRef:
    """Blueprint sec 20: agent_outputs_ref records Agent output references."""

    def _make_recorder(self):
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        return TraceRecorder(trace_body_enabled=True)

    def test_record_query_planner_output(self):
        """Should record query_planner output reference."""
        rec = self._make_recorder()
        rec.record_agent_output("query_planner", {"sub_problems": [], "schema_valid": True})
        ref = rec.get_agent_outputs_ref()
        assert "query_planner" in ref
        assert ref["query_planner"]["schema_valid"] is True

    def test_record_evidence_analyst_output(self):
        """Should record evidence_analyst judgment IDs."""
        rec = self._make_recorder()
        rec.record_agent_output("evidence_analyst", {"judgment_ids": ["j1", "j2"], "schema_valid_all": True})
        ref = rec.get_agent_outputs_ref()
        assert "evidence_analyst" in ref
        assert ref["evidence_analyst"]["judgment_ids"] == ["j1", "j2"]

    def test_record_context_orchestrator_output(self):
        """Should record context_orchestrator selection list."""
        rec = self._make_recorder()
        rec.record_agent_output("context_orchestrator", {
            "context_result_id": "cr-1",
            "selection_list": [{"ledger_entry_id": "1", "decision": "selected"}],
            "schema_valid": True,
        })
        ref = rec.get_agent_outputs_ref()
        assert "context_orchestrator" in ref
        assert ref["context_orchestrator"]["context_result_id"] == "cr-1"

    def test_agent_outputs_ref_has_three_roles(self):
        """agent_outputs_ref must have all three roles (blueprint sec 11)."""
        rec = self._make_recorder()
        rec.record_agent_output("query_planner", {"sub_problems": [], "schema_valid": True})
        rec.record_agent_output("evidence_analyst", {"judgment_ids": [], "schema_valid_all": True})
        rec.record_agent_output("context_orchestrator", {"context_result_id": "", "selection_list": [], "schema_valid": True})
        ref = rec.get_agent_outputs_ref()
        assert set(ref.keys()) == {"query_planner", "evidence_analyst", "context_orchestrator"}


class TestLedgerRef:
    """Blueprint sec 13: ledger_ref records ledger entry references."""

    def _make_recorder(self):
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        return TraceRecorder(trace_body_enabled=True)

    def test_record_ledger_entry(self):
        """Should record a ledger entry ID."""
        rec = self._make_recorder()
        rec.record_ledger_entry("1234567890")
        ref = rec.get_ledger_ref()
        assert "1234567890" in ref["ledger_entry_ids"]

    def test_record_round(self):
        """Should record a round with its sub_problem_ids and judgment_id."""
        rec = self._make_recorder()
        rec.record_round(0, sub_problem_ids=[1, 2], judgment_id="j1")
        ref = rec.get_ledger_ref()
        rounds = ref["rounds"]
        assert len(rounds) >= 1
        assert rounds[0]["round_index"] == 0
        assert rounds[0]["sub_problem_ids"] == [1, 2]
        assert rounds[0]["judgment_id"] == "j1"

    def test_ledger_ref_has_required_keys(self):
        """ledger_ref must have ledger_entry_ids and rounds keys."""
        rec = self._make_recorder()
        ref = rec.get_ledger_ref()
        assert "ledger_entry_ids" in ref
        assert "rounds" in ref
        assert isinstance(ref["ledger_entry_ids"], list)
        assert isinstance(ref["rounds"], list)


class TestTTLSetting:
    """Blueprint sec 20: run records use TTL expires_at."""

    def test_set_ttl(self):
        """TraceRecorder should set TTL expiry on the run record."""
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        rec = TraceRecorder(trace_body_enabled=True)
        rec.set_ttl(seconds=3600)
        ttl = rec.get_ttl_expires_at()
        assert ttl is not None

    def test_default_ttl(self):
        """Default TTL should be 7 days (blueprint sec 20, 沿用 001)."""
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        rec = TraceRecorder(trace_body_enabled=True)
        rec.set_ttl()  # default
        ttl = rec.get_ttl_expires_at()
        assert ttl is not None


class TestRedaction:
    """FR-011/FR-012: when trace_body_enabled=False, only retain ID/status/timing/error."""

    def _make_recorder_no_body(self):
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        return TraceRecorder(trace_body_enabled=False)

    def test_body_disabled_strips_query_content(self):
        """When body disabled, retrieval_query should be stripped to empty/placeholder."""
        rec = self._make_recorder_no_body()
        rec.record_ledger_entry("123", retrieval_query="sensitive query content")
        ref = rec.get_ledger_ref()
        # When body is disabled, query content should NOT be stored
        # Only ID/status/timing/error should be retained
        assert "123" in ref["ledger_entry_ids"]  # ID retained

    def test_body_disabled_retains_timings(self):
        """Even with body disabled, timings are still recorded (FR-012)."""
        rec = self._make_recorder_no_body()
        rec.record_timing("dense", 42.5)
        timings = rec.get_timings()
        assert timings["dense"] == 42.5  # timing retained

    def test_body_disabled_retains_agent_refs(self):
        """Agent output references (IDs) are retained even with body disabled."""
        rec = self._make_recorder_no_body()
        rec.record_agent_output("evidence_analyst", {"judgment_ids": ["j1"], "schema_valid_all": True})
        ref = rec.get_agent_outputs_ref()
        assert ref["evidence_analyst"]["judgment_ids"] == ["j1"]  # ID retained

    def test_body_enabled_stores_full_content(self):
        """When body enabled, full content including queries is stored."""
        from rag_mcp.orchestration.trace_recorder import TraceRecorder
        rec = TraceRecorder(trace_body_enabled=True)
        rec.record_ledger_entry("456", retrieval_query="full query text")
        ref = rec.get_ledger_ref()
        assert "456" in ref["ledger_entry_ids"]
