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
                    "graph_hop": {"type": "integer", "minimum": 1, "maximum": 3},
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

    DECOMPOSE_SYSTEM_PROMPT = (
        "You are a query-planning agent for a code/knowledge retrieval system. "
        "Decompose the user's retrieval query into traceable sub-problems. "
        "For multi-hop questions produce one sub-problem per hop; for "
        "single-intent questions return exactly ONE sub-problem.\n"
        "\n"
        "Signal selection rules (apply per sub-problem, T073/FR-001):\n"
        "- 'dense' and 'sparse' recall chunks by semantic/lexical similarity "
        "to the query text. They are the right signals for precision questions: "
        "exact symbols or definitions, column/type/constraint/index/view "
        "declarations, compatibility or consistency checks between named "
        "items, version or source conflicts, configuration values, "
        "'what/which fields does X have'.\n"
        "- 'graph' traverses structural relations (method call edges, "
        "foreign-key edges). Add 'graph' ONLY when the question itself asks "
        "about relationships or traversal: who calls X, which methods X "
        "invokes, which tables reference a table/column, callers/callees, "
        "multi-hop chains across symbols or tables. Do NOT add 'graph' when "
        "the question merely names a symbol, table or column but asks about "
        "its content, definition or compatibility — use 'dense' and 'sparse' "
        "there.\n"
        "- Always include 'dense'; add 'sparse' when the query names "
        "concrete identifiers (class, method, table, column, constraint "
        "names).\n"
        "\n"
        "'relation_directions' (optional, only when 'graph' is in signals) "
        "is a subset of [\"calls\", \"called_by\", \"fk_references\", "
        "\"fk_referenced_by\"]. Pick the minimal direction the question "
        "needs: who calls X -> [\"called_by\"]; what does X call -> "
        "[\"calls\"]; FK-reference questions -> [\"fk_references\", "
        "\"fk_referenced_by\"].\n"
        "'graph_hop' (optional integer 1-3, only when 'graph' is in "
        "signals): 1 for direct relations, 2 for one intermediate hop. "
        "Omit when unsure.\n"
        "\n"
        "Respond with ONLY a JSON object of the exact shape:\n"
        '{"sub_problems": [{"query": string, "signals": [string], '
        '"relation_directions": [string]}]}\n'
        "No markdown fences, no extra keys, no commentary."
    )

    def __init__(self, model_and_version: str = "", llm_client=None) -> None:
        super().__init__(model_and_version=model_and_version)
        self._sub_problem_counter = 0
        self._llm_client = llm_client

    def get_default_directions(self) -> list[str]:
        """Return the 004 deterministic bidirectional default (FR-033)."""
        return list(BIDIRECTIONAL_DEFAULT)

    def _next_sub_problem_id(self) -> int:
        """Return the next sub_problem_id (starts from 1, monotonic, FR-032)."""
        self._sub_problem_counter += 1
        return self._sub_problem_counter

    def _llm_decompose(self, query: str, context: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Call the LLM to decompose the query (overridable for testing).

        Makes a REAL LLM call through the wired client when one is configured
        (T019 LLM integration). Returns a list of sub-problem dicts, or None
        on any failure — the caller then degrades deterministically (SC-011).
        Each dict has: query (str), signals (list[str]), relation_directions (list[str]|None).
        """
        if self._llm_client is None:
            # No client wired: deterministic fallback (keeps unit tests offline)
            return None
        try:
            payload = self._llm_client.chat_json(
                self.DECOMPOSE_SYSTEM_PROMPT,
                {"query": query, "task_context": context.get("task_context")},
            )
        except Exception as exc:
            logger.warning("query_planner LLM call raised: %s", exc)
            return None
        if not isinstance(payload, dict):
            return None
        sub_problems = payload.get("sub_problems")
        if not isinstance(sub_problems, list) or not sub_problems:
            return None
        return sub_problems

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
            if "graph" in signals:
                # Planner hop cap within the 004 guardrail band; invalid or
                # missing values fall back to the 004 default hop 2 (FR-033)
                sub_problem["graph_hop"] = self._validate_hop(sp.get("graph_hop"))
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

    def _validate_hop(self, value: Any) -> int:
        """Validate the planner graph hop cap (FR-033, T067).

        The 004 guardrail band is 1..3 (hop_default 2 / hop_max 3). Invalid
        or missing values fall back to the 004 deterministic default 2.
        """
        try:
            hop = int(value)
        except (TypeError, ValueError):
            return 2
        if hop < 1 or hop > 3:
            return 2
        return hop

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
