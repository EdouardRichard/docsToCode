"""Tests for context orchestration parent-context supplementation (T065 Red).

Evidence that needs parent context pulls the parent scope in on demand
(reusing the 001 parent backfill capability); parent supplementation never
exceeds the boxing cap (FR-017/US3-AC2); the decision enters the selection
list and stays traceable.

This test MUST FAIL before parent supplementation exists (TDD Red).
"""

from __future__ import annotations

import pytest


def _candidate(eid, score, parent=None, ledger_id="", source_id="s1"):
    return {
        "evidence_id": eid,
        "chunk_id": eid,
        "sub_problem_id": 1,
        "sub_problem_ids": [1],
        "retrieval_query": "q",
        "retrievers": ["dense"],
        "score": score,
        "source_id": source_id,
        "source_version": 1,
        "source_position": f"doc#{eid}",
        "knowledge_scope_id": 100,
        "knowledge_scope_type": "project",
        "project_id": 1,
        "index_version": 1,
        "content_excerpt": f"content {eid}",
        "ledger_entry_id": ledger_id,
        "parent": parent,
    }


def _parent_meta(pid):
    return {
        "chunk_id": pid,
        "content_excerpt": f"parent content {pid}",
        "position_path": f"doc#{pid}",
        "source_id": "s1",
        "source_version": 1,
        "knowledge_scope_id": 100,
        "knowledge_scope_type": "project",
        "project_id": 1,
        "index_version": 1,
    }


def _make_machine(candidates, top_k=5):
    from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
    from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
    from rag_mcp.agents.query_planner import QueryPlannerAgent
    from rag_mcp.orchestration.state_machine import AgenticStateMachine

    planner = QueryPlannerAgent(model_and_version="t")
    planner._llm_decompose = lambda q, ctx: [{"query": "q", "signals": ["dense"]}]
    analyst = EvidenceAnalystAgent(model_and_version="t")
    analyst._llm_judge = lambda ctx: {
        "coverage_state": "covered",
        "conflict_type": "none",
        "uncovered_sub_problem_ids": [],
        "needs_supplementary": False,
        "gap_descriptions": [],
    }

    machine = AgenticStateMachine(
        run_id="880", request_id="req-parent",
        project_scope=["proj-a"], knowledge_scope_ids=["100"],
    )
    machine.set_query_planner(planner)
    machine.set_evidence_analyst(analyst)
    machine.set_context_orchestrator(ContextOrchestratorAgent(model_and_version="t"))

    class _FakePipeline:
        async def retrieve_round(self, sub_problems, scope_ids, round_index):
            return {
                "candidates": candidates,
                "subpath_timings": {},
                "failed_paths": [],
                "graph_used": False,
            }

    machine.set_retrieval_pipeline(_FakePipeline())
    return machine


class TestParentSupplementation:
    @pytest.mark.asyncio
    async def test_parent_pulled_in_when_selected_child_needs_it(self):
        cands = [_candidate("c1", 0.9, parent=_parent_meta("p1"))]
        machine = _make_machine(cands)
        await machine.run_async(context={"query": "q", "scope_ids": [100], "top_k": 5})

        ids = {c["evidence_id"] for c in machine.get_candidates()}
        assert "p1" in ids, "parent scope must be supplemented into final context"

        record = machine.get_state_envelope().to_dict()
        sel = record["agent_outputs_ref"]["context_orchestrator"]["selection_list"]
        parent_entries = [s for s in sel if s.get("evidence_id") == "p1"]
        assert parent_entries, "parent supplement must be traceable in selection list"
        assert parent_entries[0]["decision"] == "selected"

    @pytest.mark.asyncio
    async def test_parent_never_exceeds_boxing_cap(self):
        # top_k=2 with two selected children -> no room for parents
        cands = [
            _candidate("c1", 0.9, parent=_parent_meta("p1")),
            _candidate("c2", 0.8, parent=_parent_meta("p2")),
        ]
        machine = _make_machine(cands, top_k=2)
        await machine.run_async(context={"query": "q", "scope_ids": [100], "top_k": 2})

        record = machine.get_state_envelope().to_dict()
        sel = record["agent_outputs_ref"]["context_orchestrator"]["selection_list"]
        selected = [s for s in sel if s["decision"] == "selected"]
        assert len(selected) <= 2, f"boxing cap exceeded: {selected}"

    @pytest.mark.asyncio
    async def test_parent_supplemented_within_remaining_capacity(self):
        # top_k=3, two children -> one parent fits
        cands = [
            _candidate("c1", 0.9, parent=_parent_meta("p1")),
            _candidate("c2", 0.8),
        ]
        machine = _make_machine(cands, top_k=3)
        await machine.run_async(context={"query": "q", "scope_ids": [100], "top_k": 3})

        record = machine.get_state_envelope().to_dict()
        sel = record["agent_outputs_ref"]["context_orchestrator"]["selection_list"]
        selected_ids = [s.get("evidence_id") for s in sel if s["decision"] == "selected"]
        assert "p1" in selected_ids

    @pytest.mark.asyncio
    async def test_no_parent_no_supplement(self):
        cands = [_candidate("c1", 0.9)]
        machine = _make_machine(cands)
        await machine.run_async(context={"query": "q", "scope_ids": [100], "top_k": 5})
        record = machine.get_state_envelope().to_dict()
        sel = record["agent_outputs_ref"]["context_orchestrator"]["selection_list"]
        selected_ids = [s.get("evidence_id") for s in sel if s["decision"] == "selected"]
        assert selected_ids == ["c1"]

    @pytest.mark.asyncio
    async def test_parent_not_duplicated_when_already_selected(self):
        # parent chunk is itself a recalled candidate -> no duplicate
        cands = [
            _candidate("p1", 0.95),
            _candidate("c1", 0.9, parent=_parent_meta("p1")),
        ]
        machine = _make_machine(cands)
        await machine.run_async(context={"query": "q", "scope_ids": [100], "top_k": 5})
        ids = [c["evidence_id"] for c in machine.get_candidates()]
        assert ids.count("p1") == 1
