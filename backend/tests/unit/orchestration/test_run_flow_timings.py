"""Tests for sub-path timing and cost recording in the run flow (T063 Red).

Every run MUST record dense/sparse/graph/fusion/rerank and the three Agent
node timings plus the total LLM cost in the run record (FR-031/SC-007); the
TraceRecorder participates in the run flow with TTL and redaction honoured
(FR-011/FR-012).

This test MUST FAIL before the timing/cost wiring exists (TDD Red).
"""

from __future__ import annotations

import pytest

from rag_mcp.orchestration.state_machine import AgenticStateMachine


class _FakeLLMClient:
    """LLM client stub with usage accounting (chars)."""

    def __init__(self, prompt_chars=1200, completion_chars=600):
        self.prompt_chars = prompt_chars
        self.completion_chars = completion_chars
        self.calls = 0

    def chat_json(self, system_prompt, user_payload):
        self.calls += 1
        return None


def _make_machine(monkeypatch=None):
    from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
    from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
    from rag_mcp.agents.query_planner import QueryPlannerAgent

    planner = QueryPlannerAgent(model_and_version="m-1")
    planner._llm_client = _FakeLLMClient()
    planner._llm_decompose = lambda q, ctx: [
        {"query": "part one", "signals": ["dense"]},
    ]
    analyst = EvidenceAnalystAgent(model_and_version="m-1")
    analyst._llm_client = _FakeLLMClient()
    analyst._llm_judge = lambda ctx: {
        "coverage_state": "covered",
        "conflict_type": "none",
        "uncovered_sub_problem_ids": [],
        "needs_supplementary": False,
        "gap_descriptions": [],
    }
    orchestrator = ContextOrchestratorAgent(model_and_version="m-1")

    machine = AgenticStateMachine(
        run_id="777",
        request_id="req-t063",
        project_scope=["proj-a"],
        knowledge_scope_ids=["100"],
    )
    machine.set_query_planner(planner)
    machine.set_evidence_analyst(analyst)
    machine.set_context_orchestrator(orchestrator)

    class _FakePipeline:
        async def retrieve_round(self, sub_problems, scope_ids, round_index):
            return {
                "candidates": [{
                    "evidence_id": "ev-1",
                    "chunk_id": "ev-1",
                    "sub_problem_id": 1,
                    "retrieval_query": "part one",
                    "retrievers": ["dense", "sparse"],
                    "score": 0.8,
                    "source_id": "s1",
                    "source_version": 1,
                    "source_position": "doc#sec",
                    "knowledge_scope_id": 100,
                    "knowledge_scope_type": "project",
                    "project_id": 1,
                    "index_version": 1,
                    "content_excerpt": "excerpt",
                }],
                "subpath_timings": {
                    "dense_recall_ms": 3.0,
                    "sparse_recall_ms": 3.0,
                    "fusion_ms": 0.4,
                    "rerank_ms": 1.2,
                },
                "failed_paths": [],
                "graph_used": False,
            }

    machine.set_retrieval_pipeline(_FakePipeline())
    return machine


class TestSubPathTimings:
    @pytest.mark.asyncio
    async def test_agent_node_timings_recorded(self):
        machine = _make_machine()
        record = await machine.run_async(context={"query": "q", "scope_ids": [100]})
        timings = record["sub_path_timings"]
        for key in ("query_planner_ms", "evidence_analyst_ms", "context_orchestrator_ms"):
            assert key in timings, f"missing agent node timing {key}"
            assert timings[key] >= 0

    @pytest.mark.asyncio
    async def test_retrieval_subpath_timings_recorded(self):
        machine = _make_machine()
        record = await machine.run_async(context={"query": "q", "scope_ids": [100]})
        timings = record["sub_path_timings"]
        for key in ("dense_recall_ms", "sparse_recall_ms", "fusion_ms", "rerank_ms"):
            assert key in timings, f"missing retrieval timing {key}"
            assert timings[key] > 0


class TestCostRecording:
    @pytest.mark.asyncio
    async def test_total_cost_recorded_when_price_configured(self, monkeypatch):
        monkeypatch.setenv("AGENTIC_LLM_PRICE_PER_MILLION", "1.0")
        machine = _make_machine()
        record = await machine.run_async(context={"query": "q", "scope_ids": [100]})
        assert record.get("total_cost") is not None
        assert record["total_cost"] > 0

    @pytest.mark.asyncio
    async def test_total_cost_zero_when_price_unset(self, monkeypatch):
        monkeypatch.delenv("AGENTIC_LLM_PRICE_PER_MILLION", raising=False)
        machine = _make_machine()
        record = await machine.run_async(context={"query": "q", "scope_ids": [100]})
        assert record.get("total_cost") == pytest.approx(0.0)


class TestTraceRecorderInFlow:
    @pytest.mark.asyncio
    async def test_trace_populated_with_ttl(self):
        machine = _make_machine()
        await machine.run_async(context={"query": "q", "scope_ids": [100]})
        trace = machine.get_trace()
        assert trace["sub_path_timings"], "trace must carry sub-path timings"
        assert trace["ledger_ref"]["ledger_entry_ids"] == [] or isinstance(
            trace["ledger_ref"]["ledger_entry_ids"], list
        )
        assert trace["ttl_expires_at"], "TTL must be set on the trace (blueprint 20)"

    @pytest.mark.asyncio
    async def test_trace_redaction_when_body_disabled(self, monkeypatch):
        monkeypatch.setenv("AGENTIC_TRACE_BODY_ENABLED", "false")
        machine = _make_machine()
        await machine.run_async(context={"query": "q", "scope_ids": [100]})
        trace = machine.get_trace()
        qp = trace["agent_outputs_ref"].get("query_planner", {})
        # Redacted: sub_problem ids retained, query bodies stripped (FR-012)
        assert "sub_problems" in qp
        for sp in qp["sub_problems"]:
            assert "query" not in sp
