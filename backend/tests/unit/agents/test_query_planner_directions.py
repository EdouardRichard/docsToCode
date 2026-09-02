"""Unit test for relation-direction selection (T020 Red, US1).

Tests that the query planner respects 004 bidirectional default and falls
back to it on invalid selections:
  - Default direction = calls+called_by / fk_references+fk_referenced_by (FR-033)
  - Invalid direction selections -> fallback to 004 deterministic bidirectional default

This test MUST FAIL before direction selection is implemented (TDD Red).
"""

from __future__ import annotations

import pytest


BIDIRECTIONAL_DEFAULT = ["calls", "called_by", "fk_references", "fk_referenced_by"]
VALID_DIRECTIONS = {"calls", "called_by", "fk_references", "fk_referenced_by"}


class TestDirectionDefault:
    """FR-033: default direction is bidirectional (004 deterministic default)."""

    def test_default_directions_include_calls_and_called_by(self):
        """Default directions must include calls + called_by (004 bidirectional)."""
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        planner = QueryPlannerAgent(model_and_version="test-v1")
        dirs = planner.get_default_directions()
        assert "calls" in dirs
        assert "called_by" in dirs

    def test_default_directions_include_fk_pairs(self):
        """Default directions must include fk_references + fk_referenced_by."""
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        planner = QueryPlannerAgent(model_and_version="test-v1")
        dirs = planner.get_default_directions()
        assert "fk_references" in dirs
        assert "fk_referenced_by" in dirs

    def test_default_directions_are_all_valid(self):
        """All default directions must be in the valid set."""
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        planner = QueryPlannerAgent(model_and_version="test-v1")
        dirs = planner.get_default_directions()
        for d in dirs:
            assert d in VALID_DIRECTIONS, f"Invalid default direction: {d}"


class TestDirectionFallback:
    """FR-033: invalid direction selections fall back to 004 deterministic default."""

    def _make_planner(self, llm_response=None):
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        planner = QueryPlannerAgent(model_and_version="test-v1")
        if llm_response is not None:
            planner._llm_decompose = lambda query, context: llm_response
        return planner

    def test_invalid_direction_falls_back_to_default(self):
        """Invalid direction should fall back to the 004 bidirectional default."""
        planner = self._make_planner(llm_response=[
            {"query": "q", "signals": ["graph"], "relation_directions": ["bogus_direction"]},
        ])
        result = planner.run({"query": "query"})
        sp = result.output["sub_problems"][0]
        dirs = sp.get("relation_directions", [])
        # Should have fallen back to default
        assert "calls" in dirs or "called_by" in dirs or \
               "fk_references" in dirs or "fk_referenced_by" in dirs

    def test_empty_directions_uses_default(self):
        """Empty relation_directions should use the default bidirectional set."""
        planner = self._make_planner(llm_response=[
            {"query": "q", "signals": ["graph"], "relation_directions": []},
        ])
        result = planner.run({"query": "query"})
        sp = result.output["sub_problems"][0]
        dirs = sp.get("relation_directions", [])
        # Empty should be replaced with defaults
        assert len(dirs) > 0
        for d in dirs:
            assert d in VALID_DIRECTIONS

    def test_missing_directions_uses_default(self):
        """Missing relation_directions field should use default."""
        planner = self._make_planner(llm_response=[
            {"query": "q", "signals": ["graph"]},  # no relation_directions
        ])
        result = planner.run({"query": "query"})
        sp = result.output["sub_problems"][0]
        dirs = sp.get("relation_directions", [])
        assert len(dirs) > 0
        assert "calls" in dirs

    def test_valid_directions_preserved(self):
        """Valid direction selections should be preserved (not overwritten)."""
        planner = self._make_planner(llm_response=[
            {"query": "q", "signals": ["graph"], "relation_directions": ["calls", "called_by"]},
        ])
        result = planner.run({"query": "query"})
        sp = result.output["sub_problems"][0]
        dirs = sp.get("relation_directions", [])
        assert "calls" in dirs
        assert "called_by" in dirs

    def test_partial_invalid_falls_back(self):
        """If any direction is invalid, the whole set falls back to default."""
        planner = self._make_planner(llm_response=[
            {"query": "q", "signals": ["graph"], "relation_directions": ["calls", "bogus"]},
        ])
        result = planner.run({"query": "query"})
        sp = result.output["sub_problems"][0]
        dirs = set(sp.get("relation_directions", []))
        # Should have fallen back to full default set
        assert dirs == set(BIDIRECTIONAL_DEFAULT)

    def test_non_graph_signals_no_directions(self):
        """Non-graph signals (dense/sparse only) do not need directions."""
        planner = self._make_planner(llm_response=[
            {"query": "q", "signals": ["dense", "sparse"]},
        ])
        result = planner.run({"query": "query"})
        sp = result.output["sub_problems"][0]
        # Directions may be empty for non-graph signals
        # But if present, must be valid
        dirs = sp.get("relation_directions", [])
        for d in dirs:
            assert d in VALID_DIRECTIONS
