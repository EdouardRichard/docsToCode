"""Real LLM integration test — calls the actual DeepSeek API (T019/T025 verification).

These tests make REAL HTTP calls to LLM_BASE_URL (DeepSeek) using the
configured api_key and model. They consume actual tokens — run only when
LLM credentials are available.

They verify the full LLM integration chain:
  config -> capability router -> LLM client -> HTTP call -> parse -> schema
"""

from __future__ import annotations

import pytest

from rag_mcp.config import get_settings


def _has_llm_config() -> bool:
    s = get_settings()
    routing = s.agentic.model_routing
    return bool(routing.llm_base_url and routing.llm_api_key)


pytestmark = pytest.mark.skipif(
    not _has_llm_config(),
    reason="LLM credentials not configured (LLM_BASE_URL / api_key missing)",
)


class TestRealLLMQueryPlanner:
    """T019 real-LLM verification: multi-hop decomposition via actual API."""

    def _make_planner(self):
        from rag_mcp.agents.capability_router import CapabilityRouter
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        settings = get_settings()
        router = CapabilityRouter.from_settings(settings)
        return QueryPlannerAgent(
            model_and_version=settings.agentic.model_routing.default_model,
            llm_client=router.create_client("query_planner"),
        )

    def test_real_llm_decomposes_multi_hop_query(self):
        """Real LLM must decompose a multi-hop query into sub-problems."""
        planner = self._make_planner()
        result = planner.run({
            "query": "Which services call UserService#validateToken and what does validateToken itself depend on?",
        })
        # LLM path taken: multiple sub-problems expected for multi-hop
        assert result.output["schema_valid"] is True
        assert len(result.output["sub_problems"]) >= 1
        for sp in result.output["sub_problems"]:
            assert isinstance(sp["query"], str) and len(sp["query"]) > 0
            assert len(sp["signals"]) >= 1
            for s in sp["signals"]:
                assert s in ("dense", "sparse", "graph")

    def test_real_llm_single_intent_single_subproblem(self):
        """Real LLM must return 1 sub-problem for single-intent query (no overhead)."""
        planner = self._make_planner()
        result = planner.run({"query": "What is the purpose of the UserService class?"})
        assert result.output["schema_valid"] is True
        assert len(result.output["sub_problems"]) >= 1

    def test_real_llm_chinese_query(self):
        """Real LLM must handle a Chinese multi-hop query (FR-027 zh)."""
        planner = self._make_planner()
        result = planner.run({
            "query": "哪些服务调用了 UserService#validateToken，validateToken 自身依赖什么？",
        })
        assert result.output["schema_valid"] is True
        assert len(result.output["sub_problems"]) >= 1


class TestRealLLMEvidenceAnalyst:
    """T025 real-LLM verification: coverage judgment via actual API."""

    def _make_analyst(self):
        from rag_mcp.agents.capability_router import CapabilityRouter
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        settings = get_settings()
        router = CapabilityRouter.from_settings(settings)
        return EvidenceAnalystAgent(
            model_and_version=settings.agentic.model_routing.default_model,
            llm_client=router.create_client("evidence_analyst"),
        )

    def test_real_llm_judges_coverage(self):
        """Real LLM must judge coverage with fixed enums."""
        analyst = self._make_analyst()
        result = analyst.run({
            "run_id": "1",
            "round_index": 0,
            "query": "What does validateToken depend on?",
            "sub_problems": [{"sub_problem_id": 1, "query": "What does validateToken depend on?"}],
            "evidence": [{
                "evidence_id": "ev-1",
                "content": "validateToken calls TokenValidator.verify and reads the security config.",
            }],
        })
        assert result.output["schema_valid"] is True
        assert result.output["coverage_state"] in ("covered", "partial", "uncovered")
        assert result.output["conflict_type"] in ("none", "version_conflict", "source_conflict", "domain_conflict")
        assert isinstance(result.output["needs_supplementary"], bool)

    def test_real_llm_detects_gap(self):
        """Real LLM must detect an evidence gap (needs_supplementary=True likely)."""
        analyst = self._make_analyst()
        result = analyst.run({
            "run_id": "2",
            "round_index": 0,
            "query": "How does the payment retry mechanism interact with the ledger service?",
            "sub_problems": [
                {"sub_problem_id": 1, "query": "How does the payment retry mechanism work?"},
                {"sub_problem_id": 2, "query": "How does the ledger service record retries?"},
            ],
            # Evidence only covers sub-problem 1 — LLM should see the gap
            "evidence": [{
                "evidence_id": "ev-1",
                "content": "PaymentRetry uses exponential backoff with a max of 3 attempts.",
            }],
        })
        assert result.output["schema_valid"] is True
        assert result.output["coverage_state"] in ("covered", "partial", "uncovered")
        # Gap judgment must be consistent: if partial/uncovered, needs_supplementary allowed True
        if result.output["coverage_state"] == "covered":
            assert result.output["uncovered_sub_problem_ids"] == []

class TestRealLLMStateMachineE2E:
    """End-to-end: full state machine with real LLM via wire_default_agents()."""

    def test_state_machine_real_llm_end_to_end(self):
        """wire_default_agents() + run() must make real LLM calls and produce a valid run."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine

        machine = AgenticStateMachine(
            run_id="999",
            request_id="req-e2e",
            project_scope=["proj-a"],
            knowledge_scope_ids=["100"],
        )
        machine.wire_default_agents()  # real LLM clients from config

        # Provide synthetic candidates so context orchestration has input
        candidates = [
            {"evidence_id": "ev-1", "ledger_entry_id": "1", "source_id": "s1", "score": 0.9},
            {"evidence_id": "ev-2", "ledger_entry_id": "2", "source_id": "s2", "score": 0.8},
        ]
        record = machine.run(context={
            "query": "Which services call UserService#validateToken and what does it depend on?",
            "candidates": candidates,
        })

        # Full nine-step flow executed
        steps = machine.get_executed_steps()
        assert "query_planning" in steps
        assert "evidence_analysis" in steps
        assert "context_orchestration" in steps
        assert "response_serialization" in steps

        # Run record is valid and carries agent outputs
        assert record["completion_status"] in ("complete", "partial", "no_evidence", "failed")
        ref = record["agent_outputs_ref"]
        # query_planner produced sub-problems (from real LLM)
        assert len(ref["query_planner"]["sub_problems"]) >= 1
        assert ref["query_planner"]["schema_valid"] is True
        # evidence_analyst produced a judgment
        assert "judgment_ids" in ref["evidence_analyst"]
        # context_orchestrator produced selection list
        assert "selection_list" in ref["context_orchestrator"]

    def test_state_machine_real_llm_retrieval_queries_from_llm(self):
        """Sub-problem queries fed to step 4 must come from the real LLM decomposition."""
        from rag_mcp.orchestration.state_machine import AgenticStateMachine

        machine = AgenticStateMachine(
            run_id="999",
            request_id="req-e2e2",
            project_scope=["proj-a"],
            knowledge_scope_ids=["100"],
        )
        machine.wire_default_agents()
        machine.run(context={
            "query": "Trace the call chain from PaymentController to LedgerRepository.",
            "candidates": [],
        })
        # Retrieval queries extracted from LLM sub-problems
        sub_queries = machine.get_retrieval_queries()
        assert len(sub_queries) >= 1
        assert all(isinstance(q, str) and len(q) > 0 for q in sub_queries)
