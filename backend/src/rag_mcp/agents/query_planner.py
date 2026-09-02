"""Query planner agent for multi-hop query decomposition (T019/T021, US1).

Decomposes complex/multi-hop queries into traceable sub-problems:
  - sub_problem_id starts from 1, monotonic (FR-032)
  - signals subset of {dense, sparse, graph} (FR-001)
  - relation_directions respect 004 bidirectional default (FR-033)
  - Invalid direction selections fall back to 004 deterministic default
  - schema_valid=true when output passes validation (FR-003)
  - Single-intent query produces 1 sub-problem (no extra overhead)

Constitution VI: the query planner is an Agent whose output is an INPUT
to the deterministic controller, not an exclusive jump authority.
"""

from __future__ import annotations

import logging
from typing import Any

from rag_mcp.agents.base import AgentBase, AgentResult

logger = logging.getLogger(__name__)

VALID_SIGNALS = {"dense", "sparse", "graph"}
BIDIRECTIONAL_DEFAULT = ["calls", "called_by", "fk_references", "fk_referenced_by"]
VALID_DIRECTIONS = {"calls", "called_by", "fk_references", "fk_referenced_by"}

NODE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "sub_problems": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sub_problem_id": {"type": "integer", "minimum": 1},
                    "query": {"type": "string", "minLength": 1},
                    "signals": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["dense", "sparse", "graph"]},
                        "minItems": 1,
                    },
                    "relation_directions": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["calls", "called_by", "fk_references", "fk_referenced_by"]},
                    },
                },
                "required": ["sub_problem_id", "query", "signals"],
                "additionalProperties": False,
            },
        },
        "schema_valid": {"type": "boolean"},
    },
    "required": ["sub_problems", "schema_valid"],
    "additionalProperties": False,
}


class QueryPlannerAgent(AgentBase):
    """Query planner agent that decomposes queries into sub-problems (FR-001).

    Uses an LLM to decompose multi-hop queries. Falls back to a single
    sub-problem (the original query) when the LLM fails or returns invalid
    output (SC-011).
    """

    ROLE = "query_planner"
    NODE_SCHEMA = NODE_SCHEMA

    def __init__(self, model_and_version: str = "") -> None:
        super().__init__(model_and_version=model_and_version)
        self._sub_problem_counter = 0

    def get_default_directions(self) -> list[str]:
        """Return the 004 deterministic bidirectional default (FR-033)."""
        return list(BIDIRECTIONAL_DEFAULT)

    def _next_sub_problem_id(self) -> int:
        """Return the next sub_problem_id (starts from 1, monotonic, FR-032)."""
        self._sub_problem_counter += 1
        return self._sub_problem_counter

    def _llm_decompose(self, query: str, context: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Call the LLM to decompose the query (overridable for testing).

        Returns a list of sub-problem dicts, or None on failure.
        Each dict has: query (str), signals (list[str]), relation_directions (list[str]|None).
        """
        # Default: no LLM available, return None to trigger fallback
        return None

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Decompose the query into sub-problems (FR-001/FR-032/FR-033)."""
        query = context.get("query", "")
        if not query:
            return {"sub_problems": [], "schema_valid": True}

        raw_sub_problems = self._llm_decompose(query, context)

        if not raw_sub_problems:
            # LLM unavailable: use fallback (single sub-problem)
            return self._build_fallback_output(query)

        # Reset counter for fresh decomposition
        self._sub_problem_counter = 0
        sub_problems = []
        for sp in raw_sub_problems:
            sub_id = self._next_sub_problem_id()
            sp_query = sp.get("query", query)
            signals = self._validate_signals(sp.get("signals", ["dense"]))
            directions = self._validate_directions(sp.get("relation_directions"), signals)
            sub_problem = {
                "sub_problem_id": sub_id,
                "query": sp_query,
                "signals": signals,
            }
            if directions:
                sub_problem["relation_directions"] = directions
            sub_problems.append(sub_problem)

        return {"sub_problems": sub_problems, "schema_valid": True}

    def fallback(self, context: dict[str, Any]) -> dict[str, Any]:
        """Deterministic fallback: single sub-problem with the original query (SC-011)."""
        query = context.get("query", "")
        return self._build_fallback_output(query)

    def _build_fallback_output(self, query: str) -> dict[str, Any]:
        """Build a valid single-sub-problem output (deterministic, SC-011)."""
        self._sub_problem_counter = 0
        sub_id = self._next_sub_problem_id()
        return {
            "sub_problems": [{
                "sub_problem_id": sub_id,
                "query": query,
                "signals": ["dense"],
                "relation_directions": list(BIDIRECTIONAL_DEFAULT),
            }],
            "schema_valid": True,
        }

    def _validate_signals(self, signals: list[str]) -> list[str]:
        """Validate signals against {dense, sparse, graph} (FR-001)."""
        valid = [s for s in signals if s in VALID_SIGNALS]
        if not valid:
            valid = ["dense"]  # default fallback
        return valid

    def _validate_directions(
        self,
        directions: list[str] | None,
        signals: list[str],
    ) -> list[str]:
        """Validate relation_directions and fall back to 004 default (FR-033).

        - If signals do not include graph, directions are optional (may be empty).
        - If directions are missing or empty and graph signal is present,
          use the 004 bidirectional default.
        - If any direction is invalid, fall back to the full default set.
        """
        has_graph = "graph" in signals

        if not directions:
            if has_graph:
                return list(BIDIRECTIONAL_DEFAULT)
            return []  # non-graph signals: no directions needed

        # Check if all directions are valid
        all_valid = all(d in VALID_DIRECTIONS for d in directions)
        if not all_valid:
            # Any invalid -> fall back to full default (FR-033)
            return list(BIDIRECTIONAL_DEFAULT)

        return list(directions)
