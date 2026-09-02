"""Unit test for query planner decomposition (T018 Red, US1).

Tests the QueryPlannerAgent that decomposes complex/multi-hop queries into
traceable sub-problems with signals and relation directions:
  - Multi-hop query -> >=1 sub-problem (FR-001)
  - sub_problem_id starts from 1, monotonic (FR-032)
  - signals subset of {dense, sparse, graph} (FR-001)
  - relation_directions subset of 004 bidirectional pairs (FR-033)
  - schema_valid=true (FR-003)

This test MUST FAIL before query_planner.py is implemented (TDD Red).
"""

from __future__ import annotations

import pytest


VALID_SIGNALS = {"dense", "sparse", "graph"}
VALID_DIRECTIONS = {"calls", "called_by", "fk_references", "fk_referenced_by"}


class TestQueryPlannerImport:
    def test_import_query_planner(self):
        """QueryPlannerAgent must be importable."""
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        assert QueryPlannerAgent is not None


class TestDecomposition:
    """FR-001: multi-hop query decomposed into traceable sub-problems."""

    def _make_planner(self, llm_response=None):
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        planner = QueryPlannerAgent(model_and_version="test-model-v1")
        if llm_response is not None:
            planner._llm_decompose = lambda query, context: llm_response
        return planner

    def test_multi_hop_query_produces_sub_problems(self):
        """Multi-hop query should produce >=1 sub-problem."""
        planner = self._make_planner(llm_response=[
            {"query": "who calls validateToken", "signals": ["dense", "graph"]},
            {"query": "what does validateToken depend on", "signals": ["dense", "sparse"]},
        ])
        result = planner.run({"query": "which services call UserService#validateToken and what does validateToken depend on"})
        assert len(result.output["sub_problems"]) >= 1

    def test_sub_problem_id_starts_from_one(self):
        """sub_problem_id must start from 1 (FR-032, data-model sec 2.1)."""
        planner = self._make_planner(llm_response=[
            {"query": "sub-problem 1", "signals": ["dense"]},
            {"query": "sub-problem 2", "signals": ["sparse"]},
        ])
        result = planner.run({"query": "multi-hop query"})
        ids = [sp["sub_problem_id"] for sp in result.output["sub_problems"]]
        assert ids[0] == 1, f"First sub_problem_id should be 1, got {ids[0]}"

    def test_sub_problem_id_monotonic(self):
        """sub_problem_id must be monotonically increasing (FR-032)."""
        planner = self._make_planner(llm_response=[
            {"query": "q1", "signals": ["dense"]},
            {"query": "q2", "signals": ["sparse"]},
            {"query": "q3", "signals": ["graph"]},
        ])
        result = planner.run({"query": "multi-hop query"})
        ids = [sp["sub_problem_id"] for sp in result.output["sub_problems"]]
        for i in range(1, len(ids)):
            assert ids[i] > ids[i-1], f"sub_problem_id not monotonic: {ids}"

    def test_signals_are_valid(self):
        """signals must be subset of {dense, sparse, graph} (FR-001)."""
        planner = self._make_planner(llm_response=[
            {"query": "q1", "signals": ["dense", "graph"]},
        ])
        result = planner.run({"query": "query"})
        for sp in result.output["sub_problems"]:
            for s in sp["signals"]:
                assert s in VALID_SIGNALS, f"Invalid signal: {s}"

    def test_relation_directions_are_valid(self):
        """relation_directions must be subset of 004 bidirectional pairs (FR-033)."""
        planner = self._make_planner(llm_response=[
            {"query": "q1", "signals": ["graph"], "relation_directions": ["calls", "called_by"]},
        ])
        result = planner.run({"query": "query"})
        for sp in result.output["sub_problems"]:
            dirs = sp.get("relation_directions", [])
            for d in dirs:
                assert d in VALID_DIRECTIONS, f"Invalid direction: {d}"

    def test_schema_valid_true(self):
        """Query planner output must have schema_valid=true (FR-003)."""
        planner = self._make_planner(llm_response=[
            {"query": "q1", "signals": ["dense"]},
        ])
        result = planner.run({"query": "query"})
        assert result.output["schema_valid"] is True

    def test_single_intent_query_produces_one_sub_problem(self):
        """Single-intent query should produce exactly 1 sub-problem (no extra overhead)."""
        planner = self._make_planner(llm_response=[
            {"query": "simple query", "signals": ["dense"]},
        ])
        result = planner.run({"query": "simple query"})
        assert len(result.output["sub_problems"]) == 1

    def test_output_has_sub_problems_and_schema_valid(self):
        """Output must have sub_problems array and schema_valid boolean."""
        planner = self._make_planner(llm_response=[
            {"query": "q", "signals": ["dense"]},
        ])
        result = planner.run({"query": "query"})
        assert "sub_problems" in result.output
        assert "schema_valid" in result.output
        assert isinstance(result.output["sub_problems"], list)
        assert isinstance(result.output["schema_valid"], bool)

    def test_each_sub_problem_has_required_fields(self):
        """Each sub-problem must have sub_problem_id, query, and signals."""
        planner = self._make_planner(llm_response=[
            {"query": "q1", "signals": ["dense"]},
            {"query": "q2", "signals": ["sparse", "graph"]},
        ])
        result = planner.run({"query": "multi-hop"})
        for sp in result.output["sub_problems"]:
            assert "sub_problem_id" in sp
            assert "query" in sp
            assert "signals" in sp
            assert isinstance(sp["query"], str) and len(sp["query"]) > 0
            assert isinstance(sp["signals"], list) and len(sp["signals"]) > 0


class TestFallback:
    """SC-011: when LLM fails or schema invalid, fall back to deterministic."""

    def test_fallback_returns_single_sub_problem(self):
        """Fallback should return a single sub-problem with the original query."""
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        planner = QueryPlannerAgent(model_and_version="test-v1")
        # Force fallback by making LLM return invalid output
        planner._llm_decompose = lambda query, context: None  # LLM fails
        result = planner.run({"query": "test query"})
        assert len(result.output["sub_problems"]) == 1
        sp = result.output["sub_problems"][0]
        assert sp["sub_problem_id"] == 1
        assert sp["query"] == "test query"
        assert "dense" in sp["signals"]

    def test_fallback_is_schema_valid(self):
        """Fallback output must still be schema-valid (SC-011)."""
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        planner = QueryPlannerAgent(model_and_version="test-v1")
        planner._llm_decompose = lambda query, context: None
        result = planner.run({"query": "test"})
        assert result.output["schema_valid"] is True
