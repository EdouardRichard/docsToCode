"""Unit test for context orchestrator (T032 Red, US3).

Tests the ContextOrchestratorAgent that produces the final context:
  - No duplicate evidence (FR-017)
  - Preserve >=1 evidence per source (diversity, FR-017)
  - Binning top_k <= 20 (FR-018)
  - selection_list decision in {selected, truncated, deduped} (FR-032)
  - truncated -> expandable evidence_id (FR-018)
  - schema_valid=true (FR-003)

This test MUST FAIL before context_orchestrator.py is implemented (TDD Red).
"""

from __future__ import annotations

import pytest


VALID_DECISIONS = {"selected", "truncated", "deduped"}


class TestContextOrchestratorImport:
    def test_import_context_orchestrator(self):
        from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
        assert ContextOrchestratorAgent is not None


class TestDeduplication:
    """FR-017: no duplicate evidence in final context."""

    def _make_orchestrator(self):
        from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
        return ContextOrchestratorAgent(model_and_version="test-v1")

    def test_duplicate_evidence_deduped(self):
        """Duplicate evidence should be deduped."""
        orch = self._make_orchestrator()
        candidates = [
            {"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9},
            {"evidence_id": "ev-1", "ledger_entry_id": "2", "source_id": "s1", "score": 0.9},  # duplicate
            {"evidence_id": "ev-2", "ledger_entry_id": "3", "source_id": "s2", "score": 0.8},
        ]
        result = orch.run({"candidates": candidates, "top_k": 20})
        selection_list = result.output["selection_list"]
        # At least one should be deduped
        decisions = [s["decision"] for s in selection_list]
        assert "deduped" in decisions

    def test_no_duplicates_in_selected(self):
        """No duplicate evidence_ids should appear in selected entries."""
        orch = self._make_orchestrator()
        candidates = [
            {"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9},
            {"evidence_id": "ev-1", "ledger_entry_id": "2", "source_id": "s1", "score": 0.9},
        ]
        result = orch.run({"candidates": candidates, "top_k": 20})
        selected = [s for s in result.output["selection_list"] if s["decision"] == "selected"]
        ev_ids = [s["evidence_id"] for s in selected if "evidence_id" in s]
        # Should not have duplicates
        assert len(ev_ids) == len(set(ev_ids))


class TestDiversity:
    """FR-017: preserve >=1 evidence per source."""

    def _make_orchestrator(self):
        from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
        return ContextOrchestratorAgent(model_and_version="test-v1")

    def test_preserves_source_diversity(self):
        """At least 1 evidence per source should be preserved."""
        orch = self._make_orchestrator()
        candidates = [
            {"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9},
            {"evidence_id": "ev-2", "ledger_entry_id": "2", "source_id": "s2", "score": 0.5},
            {"evidence_id": "ev-3", "ledger_entry_id": "3", "source_id": "s1", "score": 0.8},
        ]
        result = orch.run({"candidates": candidates, "top_k": 20})
        selected = [s for s in result.output["selection_list"] if s["decision"] == "selected"]
        # Both sources should have at least 1 selected
        source_ids = set()
        for s in selected:
            # find the source from candidates
            for c in candidates:
                if c["ledger_entry_id"] == s.get("ledger_entry_id"):
                    source_ids.add(c["source_id"])
        assert len(source_ids) >= 2


class TestBinning:
    """FR-018: binning top_k <= 20."""

    def _make_orchestrator(self):
        from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
        return ContextOrchestratorAgent(model_and_version="test-v1")

    def test_top_k_limit_enforced(self):
        """Selected count should not exceed top_k."""
        orch = self._make_orchestrator()
        candidates = [
            {"evidence_id": f"ev-{i}", "ledger_entry_id": str(i), "source_id": f"s{i%3}", "score": 1.0 - i * 0.01}
            for i in range(25)
        ]
        result = orch.run({"candidates": candidates, "top_k": 5})
        selected = [s for s in result.output["selection_list"] if s["decision"] == "selected"]
        assert len(selected) <= 5

    def test_truncated_has_evidence_id(self):
        """Truncated entries should have expandable evidence_id (FR-018)."""
        orch = self._make_orchestrator()
        candidates = [
            {"evidence_id": f"ev-{i}", "ledger_entry_id": str(i), "source_id": "s1", "score": 1.0 - i * 0.01}
            for i in range(10)
        ]
        result = orch.run({"candidates": candidates, "top_k": 3})
        truncated = [s for s in result.output["selection_list"] if s["decision"] == "truncated"]
        assert len(truncated) > 0
        for t in truncated:
            assert "evidence_id" in t or "ledger_entry_id" in t

    def test_top_k_max_20(self):
        """top_k must not exceed 20 (FR-006)."""
        orch = self._make_orchestrator()
        candidates = [
            {"evidence_id": f"ev-{i}", "ledger_entry_id": str(i), "source_id": "s1", "score": 0.5}
            for i in range(30)
        ]
        result = orch.run({"candidates": candidates, "top_k": 100})
        selected = [s for s in result.output["selection_list"] if s["decision"] == "selected"]
        assert len(selected) <= 20


class TestSelectionList:
    """FR-032: selection_list with decision enum."""

    def _make_orchestrator(self):
        from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
        return ContextOrchestratorAgent(model_and_version="test-v1")

    def test_decisions_are_valid_enum(self):
        """All decisions must be in {selected, truncated, deduped}."""
        orch = self._make_orchestrator()
        candidates = [
            {"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9},
            {"evidence_id": "ev-2", "ledger_entry_id": "2", "source_id": "s2", "score": 0.8},
        ]
        result = orch.run({"candidates": candidates, "top_k": 20})
        for s in result.output["selection_list"]:
            assert s["decision"] in VALID_DECISIONS

    def test_output_has_context_result_id(self):
        """Output must have context_result_id (FR-032)."""
        orch = self._make_orchestrator()
        result = orch.run({"candidates": [], "top_k": 20})
        assert "context_result_id" in result.output
        assert len(result.output["context_result_id"]) > 0

    def test_schema_valid_true(self):
        """Output must have schema_valid=true (FR-003)."""
        orch = self._make_orchestrator()
        result = orch.run({"candidates": [], "top_k": 20})
        assert result.output["schema_valid"] is True

    def test_selection_list_is_list(self):
        """selection_list must be a list."""
        orch = self._make_orchestrator()
        result = orch.run({"candidates": [], "top_k": 20})
        assert isinstance(result.output["selection_list"], list)
