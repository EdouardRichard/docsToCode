"""Unit test for evidence analyst judgment (T024 Red, US2).

Tests the EvidenceAnalystAgent that produces structured judgments:
  - coverage_state/conflict_type use fixed enums (FR-032)
  - uncovered_sub_problem_ids (FR-013)
  - needs_supplementary flag (FR-015)
  - Project/public conflicts surfaced (not fabricated, FR-016)
  - schema_valid=true (FR-003)

This test MUST FAIL before evidence_analyst.py is implemented (TDD Red).
"""

from __future__ import annotations

import pytest


VALID_COVERAGE = {"covered", "partial", "uncovered"}
VALID_CONFLICT = {"none", "version_conflict", "source_conflict", "domain_conflict"}


class TestEvidenceAnalystImport:
    def test_import_evidence_analyst(self):
        """EvidenceAnalystAgent must be importable."""
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        assert EvidenceAnalystAgent is not None


class TestJudgmentOutput:
    """FR-013/FR-015/FR-032: structured judgment with fixed enums."""

    def _make_analyst(self, llm_response=None):
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        analyst = EvidenceAnalystAgent(model_and_version="test-v1")
        if llm_response is not None:
            analyst._llm_judge = lambda context: llm_response
        return analyst

    def test_coverage_state_is_valid_enum(self):
        """coverage_state must be in {covered, partial, uncovered} (FR-032)."""
        analyst = self._make_analyst(llm_response={
            "coverage_state": "partial",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [1],
            "needs_supplementary": True,
            "gap_descriptions": [{"description": "missing evidence for sub-problem 1"}],
        })
        result = analyst.run({"query": "test", "sub_problems": [{"sub_problem_id": 1}]})
        assert result.output["coverage_state"] in VALID_COVERAGE

    def test_conflict_type_is_valid_enum(self):
        """conflict_type must be in {none, version_conflict, source_conflict, domain_conflict}."""
        analyst = self._make_analyst(llm_response={
            "coverage_state": "covered",
            "conflict_type": "version_conflict",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
        })
        result = analyst.run({"query": "test", "sub_problems": []})
        assert result.output["conflict_type"] in VALID_CONFLICT

    def test_has_uncovered_sub_problem_ids(self):
        """Output must have uncovered_sub_problem_ids (FR-013)."""
        analyst = self._make_analyst(llm_response={
            "coverage_state": "partial",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [1, 2],
            "needs_supplementary": True,
            "gap_descriptions": [],
        })
        result = analyst.run({"query": "test", "sub_problems": [{"sub_problem_id": 1}, {"sub_problem_id": 2}]})
        assert "uncovered_sub_problem_ids" in result.output
        assert isinstance(result.output["uncovered_sub_problem_ids"], list)

    def test_has_needs_supplementary(self):
        """Output must have needs_supplementary flag (FR-015)."""
        analyst = self._make_analyst(llm_response={
            "coverage_state": "partial",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [1],
            "needs_supplementary": True,
            "gap_descriptions": [{"description": "gap"}],
        })
        result = analyst.run({"query": "test", "sub_problems": [{"sub_problem_id": 1}]})
        assert "needs_supplementary" in result.output
        assert isinstance(result.output["needs_supplementary"], bool)

    def test_schema_valid_true(self):
        """Output must have schema_valid=true (FR-003)."""
        analyst = self._make_analyst(llm_response={
            "coverage_state": "covered",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
        })
        result = analyst.run({"query": "test", "sub_problems": []})
        assert result.output["schema_valid"] is True

    def test_has_gap_descriptions(self):
        """Output must have gap_descriptions array (FR-013)."""
        analyst = self._make_analyst(llm_response={
            "coverage_state": "partial",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [1],
            "needs_supplementary": True,
            "gap_descriptions": [{"description": "missing", "suggested_action": "search more"}],
        })
        result = analyst.run({"query": "test", "sub_problems": [{"sub_problem_id": 1}]})
        assert "gap_descriptions" in result.output
        assert isinstance(result.output["gap_descriptions"], list)

    def test_has_model_and_version(self):
        """Output must have model_and_version (FR-002)."""
        analyst = self._make_analyst(llm_response={
            "coverage_state": "covered",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
        })
        result = analyst.run({"query": "test", "sub_problems": []})
        assert "model_and_version" in result.output
        assert result.output["model_and_version"] == "test-v1"


def _make_analyst(llm_response=None):
    """Create an EvidenceAnalystAgent for testing."""
    from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
    analyst = EvidenceAnalystAgent(model_and_version="test-v1")
    if llm_response is not None:
        analyst._llm_judge = lambda context: llm_response
    return analyst


class TestConflictSurfacing:
    """FR-016: project/public conflicts surfaced (not fabricated)."""

    def test_conflict_surfaced_not_fabricated(self):
        """When project and public evidence conflict, conflict_type should be non-none (FR-016)."""
        analyst = _make_analyst(llm_response={
            "coverage_state": "partial",
            "conflict_type": "domain_conflict",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
        })
        result = analyst.run({
            "query": "test",
            "sub_problems": [],
            "has_conflict": True,
        })
        assert result.output["conflict_type"] != "none"

    def test_no_conflict_when_none(self):
        """When no conflict, conflict_type should be none."""
        analyst = _make_analyst(llm_response={
            "coverage_state": "covered",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
        })
        result = analyst.run({"query": "test", "sub_problems": [], "has_conflict": False})
        assert result.output["conflict_type"] == "none"


class TestFallback:
    """SC-011: when LLM fails, fall back to deterministic judgment."""

    def test_fallback_returns_valid_judgment(self):
        """Fallback should return a valid judgment (SC-011)."""
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        analyst = EvidenceAnalystAgent(model_and_version="test-v1")
        analyst._llm_judge = lambda context: None  # LLM fails
        result = analyst.run({"query": "test", "sub_problems": [{"sub_problem_id": 1}]})
        assert result.output["coverage_state"] in VALID_COVERAGE
        assert result.output["conflict_type"] in VALID_CONFLICT
        assert isinstance(result.output["needs_supplementary"], bool)
        assert result.output["schema_valid"] is True

    def test_fallback_does_not_block(self):
        """Fallback must not raise (SC-011, does not block state machine)."""
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        analyst = EvidenceAnalystAgent(model_and_version="test-v1")
        analyst._llm_judge = lambda context: None
        result = analyst.run({"query": "test", "sub_problems": []})
        assert result is not None


class TestNeedsSupplementaryIsInput:
    """Constitution VI: needs_supplementary is Agent judgment INPUT, not exclusive jump."""

    def test_needs_supplementary_is_boolean(self):
        """needs_supplementary must be a boolean (consumed by controller)."""
        analyst = _make_analyst(llm_response={
            "coverage_state": "partial",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [1],
            "needs_supplementary": True,
            "gap_descriptions": [],
        })
        result = analyst.run({"query": "test", "sub_problems": [{"sub_problem_id": 1}]})
        assert isinstance(result.output["needs_supplementary"], bool)
