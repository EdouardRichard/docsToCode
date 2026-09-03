"""T073 Red: query planner signal-selection guidance in the system prompt.

The planner prompt (the behavior carrier for FR-001 "选择所需检索信号") must
teach the LLM WHEN each retrieval signal helps, so that:

  - precision questions (definitions, column/type/constraint declarations,
    compatibility or version/source conflicts) select dense+sparse WITHOUT
    graph — the graph RRF term would demote the precise lexical matches
    behind structurally adjacent chunks (T073, SC-001 conflict-query fix);
  - relationship/traversal questions (who calls X, which tables reference Y,
    multi-hop chains) select graph with the minimal relation direction.

This test MUST FAIL before the guidance exists (TDD Red for T073).
"""

from __future__ import annotations

from rag_mcp.agents.query_planner import QueryPlannerAgent


class TestPlannerSignalSelectionGuidance:
    """The DECOMPOSE_SYSTEM_PROMPT must contain signal-selection rules."""

    def _prompt(self) -> str:
        return QueryPlannerAgent.DECOMPOSE_SYSTEM_PROMPT

    def test_prompt_has_signal_selection_rules_section(self):
        prompt = self._prompt()
        assert "Signal selection rules" in prompt, (
            "The planner system prompt must carry explicit signal-selection "
            "rules so the LLM can choose dense/sparse/graph per intent (FR-001, T073)"
        )

    def test_prompt_guides_precision_questions_to_dense_sparse(self):
        prompt = self._prompt().lower()
        assert "precision questions" in prompt, (
            "The prompt must classify precision questions (definitions, "
            "declarations, compatibility/conflict checks) as dense+sparse cases (T073)"
        )
        assert "compatibility" in prompt, (
            "The prompt must name compatibility checks as a precision-question "
            "example (the SC-001 conflict-category regression source)"
        )

    def test_prompt_restricts_graph_to_relationship_traversal(self):
        prompt = self._prompt()
        assert "ONLY when the question itself asks about relationships" in prompt, (
            "Graph must be restricted to questions that themselves ask about "
            "relationships/traversal (T073: graph on precision questions demotes "
            "the precise evidence behind graph-adjacent chunks)"
        )
        assert "Do NOT add 'graph'" in prompt, (
            "The prompt must forbid graph for questions that merely name a "
            "symbol/table/column while asking about content or definitions"
        )

    def test_prompt_requires_dense_baseline(self):
        prompt = self._prompt()
        assert "Always include 'dense'" in prompt, (
            "Every sub-problem must include the dense signal (hybrid backbone)"
        )

    def test_prompt_guides_minimal_relation_directions(self):
        prompt = self._prompt()
        assert "minimal direction" in prompt, (
            "The prompt must guide minimal relation-direction selection (FR-033)"
        )
