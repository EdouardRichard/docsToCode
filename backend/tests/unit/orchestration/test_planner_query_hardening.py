"""Tests for the controller query-set hardening (T069, SC-002/SC-015).

The deterministic controller (Constitution VI) guarantees the ORIGINAL
user query always participates in retrieval:

  - exactly ONE planner sub-problem (single intent) -> the sub-problem
    query text is replaced with the ORIGINAL query, so the retrieval is
    byte-identical to the deterministic baseline (spec edge case: the
    path MUST degrade to a single deterministic retrieval with no extra
    overhead beyond signal selection)
  - TWO OR MORE sub-problems (multi intent) -> the original query is
    PREPENDED as sub-problem 1 (baseline parity anchor) and the LLM's
    sub-problems are renumbered 2..N+1

This stops the systematic rank regressions where the planner's verbose
reformulation changed dense/sparse/rerank inputs for single-intent
queries (report q2/q3/q4 pattern).

This test MUST FAIL before the hardening exists (TDD Red).
"""

from __future__ import annotations

from rag_mcp.agents.base import AgentResult
from rag_mcp.orchestration.state_machine import AgenticStateMachine


class _StubPlanner:
    """Planner stub returning a canned LLM-style decomposition."""

    def __init__(self, sub_problems: list[dict]) -> None:
        self._sub_problems = sub_problems

    def run(self, context: dict) -> AgentResult:
        return AgentResult(
            output={"sub_problems": self._sub_problems, "schema_valid": True},
            schema_valid=True,
        )


def _machine(planner) -> AgenticStateMachine:
    machine = AgenticStateMachine(
        run_id="1",
        request_id="req-1",
        project_scope=["proj-1"],
        knowledge_scope_ids=["100"],
    )
    machine.set_query_planner(planner)
    return machine


class TestSingleIntentHardening:
    def test_single_sub_problem_uses_original_query(self):
        original = "What does com.example.service.UserService#findById do?"
        planner = _StubPlanner([{
            "sub_problem_id": 1,
            "query": "Verbose LLM reformulation of the findById question",
            "signals": ["dense", "sparse"],
        }])
        machine = _machine(planner)
        machine.run({"query": original})

        assert machine.get_retrieval_queries() == [original], (
            "single-intent queries MUST retrieve with the original text "
            "(baseline parity, spec single-intent edge case)"
        )

    def test_single_sub_problem_keeps_planner_signals(self):
        original = "Explain the code at X#getActiveUsers."
        planner = _StubPlanner([{
            "sub_problem_id": 1,
            "query": "reformulated",
            "signals": ["dense", "graph"],
            "relation_directions": ["calls"],
            "graph_hop": 2,
        }])
        machine = _machine(planner)
        machine.run({"query": original})

        envelope = machine.get_state_envelope().to_dict()
        sub = envelope["agent_outputs_ref"]["query_planner"]["sub_problems"][0]
        assert sub["query"] == original
        assert sub["signals"] == ["dense", "graph"]
        assert sub["relation_directions"] == ["calls"]
        assert sub["graph_hop"] == 2

    def test_reformulation_regression_pattern_fixed(self):
        """The report q2/q3/q4 pattern: reformulated query changed ranking."""
        original = "Show me the implementation of X#repository."
        planner = _StubPlanner([{
            "sub_problem_id": 1,
            "query": "Show the implementation (field or method body) of member in class",
            "signals": ["dense"],
        }])
        machine = _machine(planner)
        machine.run({"query": original})
        queries = machine.get_retrieval_queries()
        assert queries == [original]


class TestMultiIntentHardening:
    def test_original_query_prepended_and_renumbered(self):
        original = "Which methods call X#validateToken and what does it depend on?"
        planner = _StubPlanner([
            {"sub_problem_id": 1, "query": "callers of validateToken", "signals": ["graph", "dense"]},
            {"sub_problem_id": 2, "query": "dependencies of validateToken", "signals": ["dense", "sparse"]},
        ])
        machine = _machine(planner)
        machine.run({"query": original})

        queries = machine.get_retrieval_queries()
        assert queries[0] == original, "original query must lead the retrieval set"
        assert queries[1:] == [
            "callers of validateToken",
            "dependencies of validateToken",
        ]

        envelope = machine.get_state_envelope().to_dict()
        subs = envelope["agent_outputs_ref"]["query_planner"]["sub_problems"]
        assert [s["sub_problem_id"] for s in subs] == [1, 2, 3], (
            "sub_problem_id stays monotonic from 1 after renumbering (FR-032)"
        )
        assert subs[0]["query"] == original
        assert subs[0]["signals"] == ["dense", "sparse"], (
            "the anchored original query runs the 002 hybrid default signals"
        )

    def test_three_sub_problems_anchor_plus_two(self):
        original = "multi hop query"
        planner = _StubPlanner([
            {"sub_problem_id": 1, "query": "a", "signals": ["dense"]},
            {"sub_problem_id": 2, "query": "b", "signals": ["dense"]},
            {"sub_problem_id": 3, "query": "c", "signals": ["sparse"]},
        ])
        machine = _machine(planner)
        machine.run({"query": original})
        assert machine.get_retrieval_queries() == [original, "a", "b", "c"]


class TestFallbackUnchanged:
    def test_no_planner_uses_original_single_query(self):
        machine = AgenticStateMachine(
            run_id="1", request_id="req-1",
            project_scope=["p"], knowledge_scope_ids=["100"],
        )
        machine.run({"query": "q"})
        assert machine.get_retrieval_queries() == ["q"]
