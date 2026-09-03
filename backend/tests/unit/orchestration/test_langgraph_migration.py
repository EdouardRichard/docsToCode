"""Tests for the LangGraph state machine migration (T062 Red).

The nine-step main flow and the bounded supplementary loop MUST be managed
by a LangGraph StateGraph (FR-004, plan decision):
  - the machine exposes a compiled LangGraph graph
  - the nine-step order is preserved
  - jump decisions stay with the deterministic controller (Constitution VI)
  - guardrails / isolation / four-state behaviour unchanged

This test MUST FAIL before the migration (TDD Red).
"""

from __future__ import annotations

import pytest


def _make_machine(max_rounds=None):
    from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
    from rag_mcp.agents.query_planner import QueryPlannerAgent
    from rag_mcp.orchestration.state_machine import AgenticStateMachine

    planner = QueryPlannerAgent(model_and_version="t")
    planner._llm_decompose = lambda q, ctx: [
        {"query": "part", "signals": ["dense"]},
    ]
    analyst = EvidenceAnalystAgent(model_and_version="t")
    analyst._llm_judge = lambda ctx: {
        "coverage_state": "covered",
        "conflict_type": "none",
        "uncovered_sub_problem_ids": [],
        "needs_supplementary": False,
        "gap_descriptions": [],
    }
    machine = AgenticStateMachine(
        run_id="995",
        request_id="req-t062",
        project_scope=["proj-a"],
        knowledge_scope_ids=["100"],
        max_rounds=max_rounds,
    )
    machine.set_query_planner(planner)
    machine.set_evidence_analyst(analyst)
    return machine


class TestLangGraphMigration:
    def test_machine_exposes_compiled_langgraph(self):
        machine = _make_machine()
        graph = machine.get_graph()
        from langgraph.graph.state import CompiledStateGraph

        assert isinstance(graph, CompiledStateGraph), (
            "state machine must be a compiled LangGraph StateGraph (FR-004)"
        )

    def test_nine_step_order_preserved(self):
        machine = _make_machine()
        machine.run(context={"query": "q"})
        assert machine.get_executed_steps() == [
            "receive_validate",
            "resolve_scope",
            "query_planning",
            "parallel_retrieval",
            "fusion_rerank",
            "evidence_analysis",
            "loop_decision",
            "context_orchestration",
            "response_serialization",
        ]

    def test_supplementary_loop_via_graph(self):
        # Analyst judges a gap: the deterministic controller (not the Agent)
        # owns the jump and loops within max_rounds (Constitution VI).
        machine = _make_machine(max_rounds=2)
        machine._evidence_analyst._llm_judge = lambda ctx: {
            "coverage_state": "partial",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [1],
            "needs_supplementary": True,
            "gap_descriptions": [],
        }
        record = machine.run(context={"query": "q"})
        assert record["rounds_completed"] == 2
        steps = machine.get_executed_steps()
        assert steps.count("query_planning") == 2
        assert steps.count("loop_decision") == 2
        # deterministic controller recorded every jump decision
        decisions = machine.controller_decisions
        assert len(decisions) == 2
        assert decisions[0]["should_continue"] is True
        assert decisions[1]["should_continue"] is False

    def test_rounds_capped_by_guardrail_in_graph(self):
        machine = _make_machine(max_rounds=1)
        machine._evidence_analyst._llm_judge = lambda ctx: {
            "coverage_state": "uncovered",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [1],
            "needs_supplementary": True,
            "gap_descriptions": [],
        }
        record = machine.run(context={"query": "q"})
        assert record["rounds_completed"] == 1

    def test_force_gap_loop_without_analyst(self):
        from rag_mcp.orchestration.state_machine import AgenticStateMachine

        machine = AgenticStateMachine(
            run_id="996", request_id="req-force",
            project_scope=["proj-a"], knowledge_scope_ids=["100"],
            max_rounds=2,
        )
        record = machine.run(context={"query": "q", "force_gap": True})
        assert record["rounds_completed"] == 2

    def test_four_state_semantics_unchanged(self):
        machine = _make_machine()
        record = machine.run(context={"query": "q"})
        assert record["completion_status"] in (
            "complete", "partial", "no_evidence", "failed",
        )
        assert record["schema_valid_all"] is True

    @pytest.mark.asyncio
    async def test_async_graph_with_pipeline(self):
        machine = _make_machine()

        class _FakePipeline:
            async def retrieve_round(self, sub_problems, scope_ids, round_index):
                return {
                    "candidates": [{
                        "evidence_id": "ev-1",
                        "chunk_id": "ev-1",
                        "sub_problem_id": 1,
                        "retrieval_query": "part",
                        "retrievers": ["dense"],
                        "score": 0.7,
                        "source_id": "s1",
                        "source_version": 1,
                        "source_position": "p",
                        "knowledge_scope_id": 100,
                        "knowledge_scope_type": "project",
                        "project_id": 1,
                        "index_version": 1,
                        "content_excerpt": "x",
                    }],
                    "subpath_timings": {},
                    "failed_paths": [],
                    "graph_used": False,
                }

        machine.set_retrieval_pipeline(_FakePipeline())
        record = await machine.run_async(context={"query": "q", "scope_ids": [100]})
        assert len(machine.get_candidates()) == 1
        assert record["completion_status"] in (
            "complete", "partial", "no_evidence", "failed",
        )
