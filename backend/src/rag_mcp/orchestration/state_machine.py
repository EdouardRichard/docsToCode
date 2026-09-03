"""LangGraph-style deterministic state machine skeleton (T013, FR-004/FR-005/FR-006).

Nine-step main state flow with bounded supplementary retrieval loop:
  1. receive_validate   - Receive and validate request (project_scope required)
  2. resolve_scope      - Resolve knowledge scope
  3. query_planning      - Query planning (decompose sub_problems, select signals)
  4. parallel_retrieval   - Parallel retrieval (Dense/Sparse/graph)
  5. fusion_rerank       - Fusion + Rerank
  6. evidence_analysis   - Evidence analysis (coverage/conflict/gap)
  7. loop_decision       - Supplementary loop decision (deterministic controller)
  8. context_orchestration- Context orchestration (dedup/diversity/binning)
  9. response_serialization- MCP response serialization

Constitution VI: the deterministic controller (not the Agent) owns jump
decisions. needs_supplementary is an Agent judgment INPUT, not an exclusive
jump authority. Guardrails enforce bounded rounds, timeouts, and binning.

This is the SKELETON: steps are stubs that record execution. Full Agent
integration (query_planner, evidence_analyst, context_orchestrator) is
wired in Phase 3-5 (T019/T023/T029/T037).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from rag_mcp.config import get_settings
from rag_mcp.orchestration.state_envelope import StateEnvelope
from rag_mcp.orchestration.trace_recorder import TraceRecorder

logger = logging.getLogger(__name__)

STEP_NAMES = [
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


def build_llm_agents(settings: Any | None = None):
    """Build the three Agents with real LLM clients wired from config.

    This is the Model Gateway bridge (blueprint sec 18): each role gets an
    actually-callable LLM client resolved by the capability router from the
    run-config / environment. Returns (query_planner, evidence_analyst,
    context_orchestrator). Context orchestrator uses deterministic logic
    (dedup/diversity/binning) and needs no LLM client.
    """
    from rag_mcp.agents.capability_router import CapabilityRouter
    from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
    from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
    from rag_mcp.agents.query_planner import QueryPlannerAgent

    if settings is None:
        settings = get_settings()
    router = CapabilityRouter.from_settings(settings)
    default_model = settings.agentic.model_routing.default_model

    query_planner = QueryPlannerAgent(
        model_and_version=default_model,
        llm_client=router.create_client("query_planner"),
    )
    evidence_analyst = EvidenceAnalystAgent(
        model_and_version=default_model,
        llm_client=router.create_client("evidence_analyst"),
    )
    context_orchestrator = ContextOrchestratorAgent(
        model_and_version=default_model,
    )
    return query_planner, evidence_analyst, context_orchestrator


class AgenticStateMachine:
    """Deterministic state machine for Agent orchestration (FR-004/FR-005/FR-006).

    The deterministic controller owns all jump decisions (Constitution VI).
    State is isolated by request_id/run_id (FR-025, blueprint sec 21.1).
    No global active project (Constitution I).
    """

    def __init__(
        self,
        run_id: str,
        request_id: str,
        project_scope: list[str],
        knowledge_scope_ids: list[str],
        max_rounds: int | None = None,
        task_context: dict[str, Any] | None = None,
    ) -> None:
        self._run_id = run_id
        self._request_id = request_id
        self._project_scope = project_scope
        self._knowledge_scope_ids = knowledge_scope_ids
        self._task_context = task_context

        # Guardrails from config (FR-006)
        settings = get_settings()
        agentic_cfg = settings.agentic
        self._max_rounds = max_rounds if max_rounds is not None else agentic_cfg.max_rounds
        self._top_k_max = agentic_cfg.guardrails.top_k_max
        self._total_timeout_ms = agentic_cfg.guardrails.total_timeout_ms
        self._node_timeout_ms = agentic_cfg.guardrails.node_timeout_ms_default

        # Agent instances (wired in T023/T029/T037)
        self._query_planner = None
        self._evidence_analyst = None
        self._context_orchestrator = None
        self._retrieval_queries: list[str] = []
        self._latest_judgment: dict[str, Any] | None = None
        self._judgment_ids: list[str] = []

        # Real retrieval pipeline (T058) + round candidate accumulation
        self._retrieval_pipeline = None
        self._planner_output: dict[str, Any] = {}
        self._round_recall: dict[str, Any] | None = None
        self._round_candidates: list[list[dict[str, Any]]] = []
        self._subpath_timings: dict[str, float] = {}
        self._failed_paths: list[str] = []
        self._scope_ids: list[int] = []

        # Parent-context supplementation state (T065)
        self._parent_supplements: list[dict[str, Any]] = []

        # Persistence wiring (T059) + ledger bridge state
        self._persistence = None
        self._ledger_entry_ids: list[str] = []
        self._ledger_rounds: list[dict[str, Any]] = []
        self._persisted_judgment_ids: set[str] = set()

        # Prompt-injection defense (T064): auditable events + detector
        self._injection_events: list[dict[str, Any]] = []
        self._injection_detector = None

        # Trace recorder in the run flow (T063): timings + refs + TTL +
        # redaction follow the run-config toggle (FR-011/FR-012)
        self._trace = TraceRecorder(
            trace_body_enabled=agentic_cfg.trace_body_enabled,
        )

        # LangGraph conditional-edge state (T062)
        self._should_continue: bool = False

        # Run state
        self._rounds_completed: int = 0
        self._completion_status: str = "failed"
        self._executed_steps: list[str] = []
        self._controller_decisions: list[dict[str, Any]] = []

        # State envelope
        self._envelope = StateEnvelope(
            run_id=run_id,
            request_id=request_id,
            project_scope=project_scope,
            knowledge_scope_ids=knowledge_scope_ids,
            task_context=task_context,
        )
        self._envelope.set_max_rounds(self._max_rounds)
        self._envelope.set_guardrail_state({
            "max_rounds": self._max_rounds,
            "top_k_max": self._top_k_max,
            "total_timeout_ms": self._total_timeout_ms,
            "node_timeout_ms": self._node_timeout_ms,
            "max_evidence_per_source": agentic_cfg.max_evidence_per_source,
            "graph_hop_default": agentic_cfg.guardrails.graph_hop_default,
            "graph_hop_max": agentic_cfg.guardrails.graph_hop_max,
            "graph_candidate_budget": agentic_cfg.guardrails.graph_candidate_budget_default,
            "graph_sub_timeout_ms": agentic_cfg.guardrails.graph_sub_timeout_ms,
        })
        self._envelope.set_run_config({
            "agentic_retrieval_enabled": agentic_cfg.enabled,
            "max_rounds": self._max_rounds,
            "node_timeout_ms": self._node_timeout_ms,
            "top_k_max": self._top_k_max,
            "max_evidence_per_source": agentic_cfg.max_evidence_per_source,
            "total_timeout_ms": self._total_timeout_ms,
            "model_routing": {
                "query_planner": agentic_cfg.model_routing.query_planner_model,
                "evidence_analyst": agentic_cfg.model_routing.evidence_analyst_model,
                "context_orchestrator": agentic_cfg.model_routing.context_orchestrator_model,
                "default_model": agentic_cfg.model_routing.default_model,
            },
        })

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def request_id(self) -> str:
        return self._request_id

    @property
    def project_scope(self) -> list[str]:
        return self._project_scope

    @property
    def max_rounds(self) -> int:
        return self._max_rounds

    @property
    def rounds_completed(self) -> int:
        return self._rounds_completed

    @property
    def top_k_max(self) -> int:
        return self._top_k_max

    @property
    def total_timeout_ms(self) -> int:
        return self._total_timeout_ms

    @property
    def node_timeout_ms(self) -> int:
        return self._node_timeout_ms

    @property
    def completion_status(self) -> str:
        return self._completion_status

    @property
    def controller_decisions(self) -> list[dict[str, Any]]:
        return self._controller_decisions

    def get_step_names(self) -> list[str]:
        """Return the nine step names in order (blueprint sec 12)."""
        return list(STEP_NAMES)

    def get_executed_steps(self) -> list[str]:
        """Return the list of steps actually executed (for tracing)."""
        return list(self._executed_steps)

    def get_state_envelope(self) -> StateEnvelope:
        """Return the state envelope with the run record (FR-010)."""
        return self._envelope

    def set_query_planner(self, planner) -> None:
        """Wire in the QueryPlannerAgent (T023, blueprint sec 12 step 3)."""
        self._query_planner = planner

    def set_evidence_analyst(self, analyst) -> None:
        """Wire in the EvidenceAnalystAgent (T029, blueprint sec 12 step 6)."""
        self._evidence_analyst = analyst

    def set_context_orchestrator(self, orchestrator) -> None:
        """Wire in the ContextOrchestratorAgent (T037, blueprint sec 12 step 8)."""
        self._context_orchestrator = orchestrator

    def set_retrieval_pipeline(self, pipeline) -> None:
        """Wire the real retrieval pipeline into steps 4/5 (T058).

        The pipeline runs 002 hybrid recall (dense+sparse+RRF+Rerank) and the
        planner-directed 004 graph expansion per sub-problem. Once wired, the
        machine MUST be executed via run_async() inside an event loop.
        """
        self._retrieval_pipeline = pipeline

    def get_candidates(self) -> list[dict[str, Any]]:
        """Return merged candidates accumulated across rounds (T058).

        Includes parent-context supplements added during orchestration
        (T065) so they flow into serialization and the selection bridge.
        """
        groups = list(self._round_candidates)
        if self._parent_supplements:
            groups.append(list(self._parent_supplements))
        if not groups:
            return []
        from rag_mcp.orchestration.retrieval_pipeline import merge_round_candidates

        return merge_round_candidates(groups)

    def get_subpath_timings(self) -> dict[str, float]:
        """Return accumulated sub-path timings (dense/sparse/graph/fusion/rerank)."""
        return dict(self._subpath_timings)

    def get_failed_paths(self) -> list[str]:
        """Return failed retrieval sub-paths recorded during the run."""
        return list(self._failed_paths)

    def get_latest_judgment(self) -> dict[str, Any] | None:
        """Return the latest evidence-analyst judgment (T057 serialization)."""
        return dict(self._latest_judgment) if self._latest_judgment else None

    def set_persistence(self, persistence) -> None:
        """Wire run-scoped persistence for the 005 runtime tables (T059)."""
        self._persistence = persistence

    def get_injection_events(self) -> list[dict[str, Any]]:
        """Return auditable prompt-injection events (T064, FR-020)."""
        return list(self._injection_events)

    def get_trace(self) -> dict[str, Any]:
        """Return the run trace assembled by the TraceRecorder (T063)."""
        ttl = self._trace.get_ttl_expires_at()
        return {
            "sub_path_timings": dict(self._subpath_timings),
            "agent_outputs_ref": self._trace.get_agent_outputs_ref(),
            "ledger_ref": self.get_ledger_ref(),
            "ttl_expires_at": ttl.isoformat() if ttl is not None else None,
            "trace_body_enabled": self._trace.trace_body_enabled,
        }

    # ------------------------------------------------------------------
    # Timing / cost accounting (T063, FR-031/SC-007)
    # ------------------------------------------------------------------

    def _record_agent_timing(self, role: str, t0: float) -> None:
        elapsed = round((time.monotonic() - t0) * 1000, 2)
        key = f"{role}_ms"
        self._subpath_timings[key] = round(
            self._subpath_timings.get(key, 0.0) + elapsed, 2,
        )
        self._trace.record_timing(role, elapsed)

    def _compute_llm_chars(self) -> int:
        """Sum prompt+completion chars across the three agents' clients."""
        chars = 0
        for agent in (self._query_planner, self._evidence_analyst, self._context_orchestrator):
            client = getattr(agent, "_llm_client", None) if agent is not None else None
            if client is not None:
                chars += int(getattr(client, "prompt_chars", 0))
                chars += int(getattr(client, "completion_chars", 0))
        return chars

    def _compute_total_cost(self) -> float:
        """Estimate total LLM cost from client usage accounting (SC-007).

        Tokens are estimated at ~3 chars/token (mixed zh/en); the price per
        million tokens comes from AGENTIC_LLM_PRICE_PER_MILLION (default 0).
        """
        try:
            price = float(os.getenv("AGENTIC_LLM_PRICE_PER_MILLION", "0") or 0)
        except ValueError:
            price = 0.0
        tokens = self._compute_llm_chars() / 3.0
        return round(tokens / 1_000_000 * price, 6)

    def get_llm_usage(self) -> dict[str, int]:
        """LLM call counts + prompt/completion chars across the wired agents.

        Mirrors the 005 real-call contract (SC-007): only the clients'
        real-call counters contribute; LLMClient cache hits never increment
        calls / prompt_chars / completion_chars, so recorded usage always
        reflects real LLM calls.
        """
        calls = 0
        prompt_chars = 0
        completion_chars = 0
        for agent in (
            self._query_planner,
            self._evidence_analyst,
            self._context_orchestrator,
        ):
            client = getattr(agent, "_llm_client", None) if agent is not None else None
            if client is not None:
                calls += int(getattr(client, "calls", 0))
                prompt_chars += int(getattr(client, "prompt_chars", 0))
                completion_chars += int(getattr(client, "completion_chars", 0))
        return {
            "llm_calls": calls,
            "llm_prompt_chars": prompt_chars,
            "llm_completion_chars": completion_chars,
        }

    def get_provider_usage(self) -> dict[str, int]:
        """Full provider usage for the run record (FR-016): embedding/rerank
        from the retrieval pipeline + LLM from the wired agents."""
        usage: dict[str, int] = {"embedding_calls": 0, "rerank_calls": 0}
        if self._retrieval_pipeline is not None:
            pipe_usage = getattr(self._retrieval_pipeline, "get_provider_usage", None)
            if callable(pipe_usage):
                usage.update(pipe_usage())
        usage.update(self.get_llm_usage())
        return usage

    def _finalize_trace_and_cost(self) -> None:
        """Populate the trace recorder and run-record cost/tokens (T063/T072)."""
        for key, value in self._subpath_timings.items():
            self._trace.record_timing(key, value)
        for role, output in self._envelope.to_dict()["agent_outputs_ref"].items():
            self._trace.record_agent_output(role, output)
        for ledger_id in self._ledger_entry_ids:
            self._trace.record_ledger_entry(ledger_id)
        for rnd in self._ledger_rounds:
            self._trace.record_round(
                rnd.get("round_index", 0),
                rnd.get("sub_problem_ids"),
                rnd.get("judgment_id"),
            )
        self._trace.set_ttl()
        self._envelope.set_total_cost(self._compute_total_cost())
        self._envelope.set_total_llm_tokens(round(self._compute_llm_chars() / 3.0, 2))
        self._envelope.set_ledger_ref(self.get_ledger_ref())

    def get_ledger_ref(self) -> dict[str, Any]:
        """Return the ledger bridge reference (FR-024/SC-009, FR-031)."""
        return {
            "ledger_entry_ids": list(self._ledger_entry_ids),
            "rounds": list(self._ledger_rounds),
        }

    def wire_default_agents(self, settings: Any | None = None) -> None:
        """Wire the three Agents with real LLM clients from config.

        Makes the agentic path actually call the LLM for query planning and
        evidence analysis (the Model Gateway bridge, blueprint sec 18).
        """
        planner, analyst, orchestrator = build_llm_agents(settings)
        self.set_query_planner(planner)
        self.set_evidence_analyst(analyst)
        self.set_context_orchestrator(orchestrator)

    def get_retrieval_queries(self) -> list[str]:
        """Return the sub-problem queries for step 4 parallel retrieval (blueprint sec 12)."""
        return list(self._retrieval_queries)

    def get_graph(self):
        """Return the compiled LangGraph StateGraph driving this machine.

        The nine-step main flow and the bounded supplementary loop are
        managed by LangGraph node transitions (FR-004, T062); jump authority
        stays with the deterministic controller (Constitution VI) — the
        conditional edge simply reads the controller's decision.
        """
        if self._retrieval_pipeline is not None:
            return self._build_async_graph()
        return self._build_sync_graph()

    # ------------------------------------------------------------------
    # LangGraph construction (T062)
    # ------------------------------------------------------------------

    def _build_sync_graph(self):
        """Compile the sync nine-step graph (no real retrieval pipeline)."""
        from langgraph.graph import END, START, StateGraph

        def receive_validate(state):
            self._step_receive_validate(state["context"])
            return state

        def resolve_scope(state):
            self._step_resolve_scope(state["context"])
            return state

        def round_start(state):
            self._rounds_completed += 1
            return state

        def query_planning(state):
            self._step_query_planning(state["context"])
            return state

        def parallel_retrieval(state):
            self._step_parallel_retrieval(state["context"])
            return state

        def fusion_rerank(state):
            self._step_fusion_rerank(state["context"])
            return state

        def evidence_analysis(state):
            self._step_evidence_analysis(state["context"])
            return state

        def loop_decision(state):
            force_gap = state["context"].get("force_gap", False)
            self._should_continue = self._step_loop_decision(
                state["context"], force_gap,
            )
            return state

        def route_after_decision(state):
            return "round_start" if self._should_continue else "context_orchestration"

        def context_orchestration(state):
            self._step_context_orchestration(state["context"])
            return state

        def response_serialization(state):
            self._step_response_serialization(state["context"])
            return state

        graph = StateGraph(dict)
        graph.add_node("receive_validate", receive_validate)
        graph.add_node("resolve_scope", resolve_scope)
        graph.add_node("round_start", round_start)
        graph.add_node("query_planning", query_planning)
        graph.add_node("parallel_retrieval", parallel_retrieval)
        graph.add_node("fusion_rerank", fusion_rerank)
        graph.add_node("evidence_analysis", evidence_analysis)
        graph.add_node("loop_decision", loop_decision)
        graph.add_node("context_orchestration", context_orchestration)
        graph.add_node("response_serialization", response_serialization)

        graph.add_edge(START, "receive_validate")
        graph.add_edge("receive_validate", "resolve_scope")
        graph.add_edge("resolve_scope", "round_start")
        graph.add_edge("round_start", "query_planning")
        graph.add_edge("query_planning", "parallel_retrieval")
        graph.add_edge("parallel_retrieval", "fusion_rerank")
        graph.add_edge("fusion_rerank", "evidence_analysis")
        graph.add_edge("evidence_analysis", "loop_decision")
        graph.add_conditional_edges(
            "loop_decision",
            route_after_decision,
            {"round_start": "round_start", "context_orchestration": "context_orchestration"},
        )
        graph.add_edge("context_orchestration", "response_serialization")
        graph.add_edge("response_serialization", END)
        return graph.compile()

    def _build_async_graph(self):
        """Compile the async graph with real retrieval + persistence nodes."""
        from langgraph.graph import END, START, StateGraph

        def receive_validate(state):
            self._step_receive_validate(state["context"])
            return state

        def resolve_scope(state):
            self._step_resolve_scope(state["context"])
            return state

        def round_start(state):
            self._rounds_completed += 1
            return state

        def query_planning(state):
            self._step_query_planning(state["context"])
            return state

        async def parallel_retrieval(state):
            await self._step_parallel_retrieval_async(
                state["context"], state.get("scope_ids", []),
            )
            return state

        def fusion_rerank(state):
            self._step_fusion_rerank(state["context"])
            return state

        async def persist_round(state):
            await self._persist_round(state.get("scope_ids", []))
            return state

        def evidence_analysis(state):
            self._step_evidence_analysis(state["context"])
            return state

        async def persist_judgment(state):
            await self._persist_judgment()
            return state

        def loop_decision(state):
            force_gap = state["context"].get("force_gap", False)
            self._should_continue = self._step_loop_decision(
                state["context"], force_gap,
            )
            return state

        def route_after_decision(state):
            return "round_start" if self._should_continue else "context_orchestration"

        def context_orchestration(state):
            self._step_context_orchestration(state["context"])
            return state

        async def persist_selections(state):
            await self._persist_selections()
            return state

        def response_serialization(state):
            self._step_response_serialization(state["context"])
            return state

        graph = StateGraph(dict)
        graph.add_node("receive_validate", receive_validate)
        graph.add_node("resolve_scope", resolve_scope)
        graph.add_node("round_start", round_start)
        graph.add_node("query_planning", query_planning)
        graph.add_node("parallel_retrieval", parallel_retrieval)
        graph.add_node("fusion_rerank", fusion_rerank)
        graph.add_node("persist_round", persist_round)
        graph.add_node("evidence_analysis", evidence_analysis)
        graph.add_node("persist_judgment", persist_judgment)
        graph.add_node("loop_decision", loop_decision)
        graph.add_node("context_orchestration", context_orchestration)
        graph.add_node("persist_selections", persist_selections)
        graph.add_node("response_serialization", response_serialization)

        graph.add_edge(START, "receive_validate")
        graph.add_edge("receive_validate", "resolve_scope")
        graph.add_edge("resolve_scope", "round_start")
        graph.add_edge("round_start", "query_planning")
        graph.add_edge("query_planning", "parallel_retrieval")
        graph.add_edge("parallel_retrieval", "fusion_rerank")
        graph.add_edge("fusion_rerank", "persist_round")
        graph.add_edge("persist_round", "evidence_analysis")
        graph.add_edge("evidence_analysis", "persist_judgment")
        graph.add_edge("persist_judgment", "loop_decision")
        graph.add_conditional_edges(
            "loop_decision",
            route_after_decision,
            {"round_start": "round_start", "context_orchestration": "context_orchestration"},
        )
        graph.add_edge("context_orchestration", "persist_selections")
        graph.add_edge("persist_selections", "response_serialization")
        graph.add_edge("response_serialization", END)
        return graph.compile()

    # ------------------------------------------------------------------
    # Execution entry points
    # ------------------------------------------------------------------

    def run(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute the nine-step flow via the compiled LangGraph (T062).

        The deterministic controller (not the Agent) decides whether to
        loop back for supplementary retrieval (Constitution VI); LangGraph
        manages node transitions (FR-004).

        When a real retrieval pipeline is wired (T058) the run needs an event
        loop: call run_async() from async code; a sync caller outside a loop
        is transparently bridged with asyncio.run().
        """
        if self._retrieval_pipeline is not None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(self.run_async(context))
            raise RuntimeError(
                "AgenticStateMachine with a wired retrieval pipeline must be "
                "executed via run_async() inside the running event loop"
            )

        context = context or {}
        graph = self._build_sync_graph()
        graph.invoke({"context": context})

        # Finalize state envelope
        self._envelope.set_rounds_completed(self._rounds_completed)
        self._envelope.set_completion_status(self._completion_status)
        self._envelope.set_sub_path_timings(dict(self._subpath_timings))
        self._finalize_trace_and_cost()

        return self._envelope.to_dict()

    async def run_async(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Async nine-step flow via the compiled async LangGraph (T058/T062).

        Steps 4/5 await the wired AgenticRetrievalPipeline (002 hybrid recall
        + planner-directed 004 graph + RRF/Rerank); persistence nodes append
        ledger/judgment/selection rows (T059). Steps, guardrails, loop
        decisions and four-state semantics are identical to run().
        """
        context = context or {}

        # Resolve scope_ids for retrieval (context wins, else envelope IDs)
        scope_ids = context.get("scope_ids")
        if not scope_ids:
            scope_ids = []
            for sid in self._knowledge_scope_ids:
                try:
                    scope_ids.append(int(sid))
                except (TypeError, ValueError):
                    continue
        self._scope_ids = list(scope_ids)

        graph = self._build_async_graph()
        await graph.ainvoke({"context": context, "scope_ids": scope_ids})

        # Finalize state envelope
        self._envelope.set_rounds_completed(self._rounds_completed)
        self._envelope.set_completion_status(self._completion_status)
        self._envelope.set_sub_path_timings(dict(self._subpath_timings))
        self._finalize_trace_and_cost()

        return self._envelope.to_dict()

    # ------------------------------------------------------------------
    # Persistence hooks (T059) — active only when a persistence is wired;
    # errors propagate so the entry layer can degrade deterministically.
    # ------------------------------------------------------------------

    async def _persist_round(self, scope_ids: list[int]) -> None:
        """Append this round's recalled candidates to the evidence ledger."""
        if self._persistence is None or not self._round_candidates:
            return
        round_candidates = self._round_candidates[-1]
        round_index = self._rounds_completed - 1
        if not round_candidates:
            self._ledger_rounds.append({
                "round_index": round_index,
                "sub_problem_ids": sorted({
                    int(sp.get("sub_problem_id", 0))
                    for sp in self._planner_output.get("sub_problems", [])
                }),
            })
            return
        id_map = await self._persistence.persist_round_candidates(
            request_id=self._request_id,
            run_id=self._run_id,
            round_index=round_index,
            candidates=round_candidates,
            scope_ids=scope_ids,
        )
        for cand in round_candidates:
            ledger_id = id_map.get(str(cand.get("evidence_id", "")))
            if ledger_id:
                cand["ledger_entry_id"] = ledger_id
                self._ledger_entry_ids.append(ledger_id)
        self._ledger_rounds.append({
            "round_index": round_index,
            "sub_problem_ids": sorted({
                int(c.get("sub_problem_id", 0)) for c in round_candidates
            }),
        })

    async def _persist_judgment(self) -> None:
        """Append the latest analyst judgment to agent_judgment."""
        if self._persistence is None or self._latest_judgment is None:
            return
        judgment_id = str(self._latest_judgment.get("judgment_id", ""))
        if judgment_id and judgment_id in self._persisted_judgment_ids:
            return
        persisted_id = await self._persistence.persist_judgment(self._latest_judgment)
        self._persisted_judgment_ids.add(persisted_id)
        # Bridge the judgment into the ledger round reference (SC-009)
        round_index = self._latest_judgment.get("round_index")
        for rnd in self._ledger_rounds:
            if rnd.get("round_index") == round_index and "judgment_id" not in rnd:
                rnd["judgment_id"] = persisted_id

    def _supplement_parent_context(
        self, context: dict[str, Any], co_output: dict[str, Any],
    ) -> None:
        """Pull parent scope in for selected evidence that needs it (T065).

        Reuses the 001 parent backfill metadata attached by the retrieval
        pipeline. Parents are supplemented only within the remaining boxing
        capacity (FR-017/US3-AC2) and each supplement is recorded as a
        'selected' entry in the selection list (traceable).
        """
        top_k = min(
            int(context.get("top_k") or self._top_k_max), self._top_k_max,
        )
        selection_list = co_output.get("selection_list", [])
        selected = [s for s in selection_list if s.get("decision") == "selected"]
        capacity = top_k - len(selected)
        if capacity <= 0:
            return

        all_candidates = self.get_candidates()
        known_ids = {c.get("evidence_id") for c in all_candidates}
        cand_by_id = {c.get("evidence_id"): c for c in all_candidates}

        for sel in selected:
            if capacity <= 0:
                break
            child = cand_by_id.get(sel.get("evidence_id"))
            parent = (child or {}).get("parent")
            if not parent:
                continue
            pid = str(parent.get("chunk_id", ""))
            if not pid or pid in known_ids:
                continue
            child_score = float((child or {}).get("score", 0.0))
            self._parent_supplements.append({
                "evidence_id": pid,
                "chunk_id": pid,
                "sub_problem_id": (child or {}).get("sub_problem_id", 1),
                "sub_problem_ids": list((child or {}).get("sub_problem_ids", [])),
                "retrieval_query": (child or {}).get("retrieval_query", ""),
                "retrievers": ["fusion"],
                "score": max(0.0, round(child_score - 0.01, 4)),
                "source_id": parent.get("source_id", ""),
                "source_version": parent.get("source_version", 1),
                "source_position": parent.get("position_path", ""),
                "knowledge_scope_id": parent.get("knowledge_scope_id", 0),
                "knowledge_scope_type": parent.get("knowledge_scope_type", "project"),
                "project_id": parent.get("project_id", 0),
                "index_version": parent.get("index_version", 1),
                "content_excerpt": parent.get("content_excerpt", ""),
                "parent_supplemented_for": (child or {}).get("evidence_id", ""),
            })
            selection_list.append({
                "ledger_entry_id": "0",  # backfilled when persistence wires the FK
                "decision": "selected",
                "evidence_id": pid,
            })
            known_ids.add(pid)
            capacity -= 1

    async def _persist_selections(self) -> None:
        """Append the step-8 selection decisions to context_selection_list.

        Parent supplements (T065) are appended to the evidence ledger first
        (referenced by the context_orchestrator) so their selection entries
        carry resolvable ledger ids.
        """
        if self._persistence is None:
            return

        co_ref = self._envelope.to_dict().get("agent_outputs_ref", {}).get(
            "context_orchestrator", {}
        )
        selection_list = co_ref.get("selection_list", [])
        context_result_id = co_ref.get("context_result_id", "")

        if self._parent_supplements:
            id_map = await self._persistence.persist_round_candidates(
                request_id=self._request_id,
                run_id=self._run_id,
                round_index=self._rounds_completed - 1,
                candidates=self._parent_supplements,
                scope_ids=self._scope_ids,
                referenced_by_agent="context_orchestrator",
            )
            for supplement in self._parent_supplements:
                ledger_id = id_map.get(str(supplement.get("evidence_id", "")))
                if ledger_id:
                    supplement["ledger_entry_id"] = ledger_id
                    self._ledger_entry_ids.append(ledger_id)
            for sel in selection_list:
                eid = str(sel.get("evidence_id", ""))
                if eid in id_map:
                    sel["ledger_entry_id"] = id_map[eid]

        if not context_result_id or not selection_list:
            return
        await self._persistence.persist_selections(
            context_result_id=context_result_id,
            run_id=self._run_id,
            selection_list=selection_list,
        )

    def _record_step(self, name: str) -> None:
        self._executed_steps.append(name)

    def _get_injection_detector(self):
        """Lazily create the injection detector (T064)."""
        if self._injection_detector is None:
            from rag_mcp.agents.injection_detector import InjectionDetector

            self._injection_detector = InjectionDetector()
        return self._injection_detector

    def _detect_injection(self, text: Any):
        """Run injection detection; failures never block retrieval (T064)."""
        try:
            return self._get_injection_detector().detect(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Injection detection unavailable: %s", exc)
            return None

    # Analyst prompt budget (T069, SC-007): bounding the evidence view
    # keeps the analyst LLM call fast so total latency stays within the
    # guardrails (fewer 30s degradations).
    ANALYST_MAX_EVIDENCE_ITEMS = 30
    ANALYST_EXCERPT_CHARS = 160

    def _build_evidence_view(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build the slim, injection-aware evidence view for the analyst (T069).

        Keeps the highest-scoring head (candidates are already score-sorted
        by the merge) and truncates each excerpt, so the analyst prompt
        stays bounded (SC-007). Injection detection still runs on the full
        available excerpt; only the prompt-facing text is truncated.
        """
        view: list[dict[str, Any]] = []
        for c in candidates[: self.ANALYST_MAX_EVIDENCE_ITEMS]:
            full_excerpt = c.get("content_excerpt", "") or ""
            evidence_id = c.get("evidence_id", "")
            report = self._detect_injection(full_excerpt)
            if report is not None and report.suspicious:
                already = any(
                    e.get("evidence_id") == evidence_id
                    for e in self._injection_events
                )
                if not already:
                    self._injection_events.append({
                        "evidence_id": evidence_id,
                        "risk_level": report.risk_level,
                        "matched_patterns": list(report.matched_patterns),
                        "round_index": self._rounds_completed - 1,
                    })
            if report is not None and report.risk_level == "high":
                excerpt = self._get_injection_detector().sanitize_for_prompt(
                    full_excerpt, source_ref=c.get("source_position", ""),
                )
            else:
                excerpt = full_excerpt
            view.append({
                "evidence_id": evidence_id,
                "content_excerpt": excerpt[: self.ANALYST_EXCERPT_CHARS],
                "score": c.get("score", 0.0),
                "source_position": c.get("source_position", ""),
                "sub_problem_ids": c.get("sub_problem_ids", []),
            })
        return view

    def _step_receive_validate(self, context: dict[str, Any]) -> None:
        """Step 1: Receive and validate request (project_scope required, FR-021)."""
        self._record_step("receive_validate")
        if not self._project_scope:
            raise ValueError("project_scope is required (FR-021, Constitution I)")

    def _step_resolve_scope(self, context: dict[str, Any]) -> None:
        """Step 2: Resolve knowledge scope from project_scope."""
        self._record_step("resolve_scope")

    def _harden_sub_problems(
        self,
        original_query: str,
        sub_problems: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Deterministic query-set hardening (T069, SC-002/SC-015).

        The controller (Constitution VI) guarantees the ORIGINAL user
        query always participates in retrieval, regardless of the
        planner's reformulation:

          - exactly ONE sub-problem (single intent): its query text is
            replaced with the original query, so retrieval is identical
            to the deterministic baseline (spec single-intent edge case
            "path MUST degrade to deterministic single retrieval").
          - TWO OR MORE sub-problems: the original query is PREPENDED as
            sub-problem 1 (baseline parity anchor, 002 hybrid default
            signals) and the planner's sub-problems are renumbered
            2..N+1 (sub_problem_id stays monotonic from 1, FR-032).

        Returns the hardened list, or None when nothing should change.
        """
        if not original_query or not sub_problems:
            return None
        if len(sub_problems) == 1:
            sp = sub_problems[0]
            if sp.get("query") == original_query:
                return None
            hardened = [dict(sp)]
            hardened[0]["query"] = original_query
            return hardened
        anchor = {
            "sub_problem_id": 1,
            "query": original_query,
            "signals": ["dense", "sparse"],
        }
        renumbered = []
        for sp in sub_problems:
            entry = dict(sp)
            entry["sub_problem_id"] = len(renumbered) + 2
            renumbered.append(entry)
        return [anchor, *renumbered]

    def _step_query_planning(self, context: dict[str, Any]) -> None:
        """Step 3: Query planning (blueprint sec 12, wired in T023).

        Calls the QueryPlannerAgent to decompose the query into
        sub-problems, then applies the deterministic query-set
        hardening (T069): the original query always participates in
        retrieval so single-intent queries keep baseline parity
        (SC-002/SC-015) and multi-intent queries keep a baseline anchor
        while decomposition adds coverage. Stores the hardened output in
        the state envelope (traceable: it is exactly what step 4 uses)
        and extracts sub-problem queries for step 4 parallel retrieval.
        """
        self._record_step("query_planning")
        if self._query_planner is not None:
            t0 = time.monotonic()
            result = self._query_planner.run(context)
            self._record_agent_timing("query_planner", t0)
            output = result.output
            hardened = self._harden_sub_problems(
                context.get("query", ""), output.get("sub_problems", []),
            )
            if hardened is not None:
                output = {**output, "sub_problems": hardened}
            # Store in state envelope
            self._envelope.set_agent_output("query_planner", output)
            self._planner_output = output
            # Extract sub-problem queries for step 4
            self._retrieval_queries = [
                sp.get("query", "") for sp in output.get("sub_problems", [])
            ]
        else:
            # No planner wired: use original query as single sub-problem
            query = context.get("query", "")
            self._retrieval_queries = [query] if query else []
            self._planner_output = {
                "sub_problems": [{"sub_problem_id": 1, "query": query, "signals": ["dense"]}] if query else [],
                "schema_valid": True,
            }
            self._envelope.set_agent_output("query_planner", self._planner_output)

    def _step_parallel_retrieval(self, context: dict[str, Any]) -> None:
        """Step 4: Parallel retrieval (sync stub - real recall in run_async)."""
        self._record_step("parallel_retrieval")

    async def _step_parallel_retrieval_async(
        self, context: dict[str, Any], scope_ids: list[int],
    ) -> None:
        """Step 4: Parallel retrieval via the real pipeline (T058).

        Each sub-problem query is recalled through the 002 hybrid stack
        (dense+sparse+RRF+Rerank) with planner-directed 004 graph expansion
        (FR-005/FR-006/FR-033). Without a wired pipeline this is a stub.
        """
        self._record_step("parallel_retrieval")
        if self._retrieval_pipeline is None:
            self._round_recall = None
            return
        sub_problems = self._planner_output.get("sub_problems") or []
        if not sub_problems:
            query = context.get("query", "")
            sub_problems = [{"sub_problem_id": 1, "query": query, "signals": ["dense"]}] if query else []
        t0 = time.monotonic()
        try:
            result = await self._retrieval_pipeline.retrieve_round(
                sub_problems=sub_problems,
                scope_ids=scope_ids,
                round_index=self._rounds_completed - 1,
            )
        except Exception as exc:  # noqa: BLE001 - degrade, never block (SC-011)
            logger.error("Agentic parallel retrieval failed: %s", exc, exc_info=True)
            self._round_recall = {
                "candidates": [],
                "subpath_timings": {
                    "parallel_retrieval_ms": round((time.monotonic() - t0) * 1000, 2),
                },
                "failed_paths": ["parallel_retrieval_failed"],
                "graph_used": False,
            }
            self._round_candidates.append([])
            return
        elapsed = round((time.monotonic() - t0) * 1000, 2)
        self._round_recall = result
        for key, value in (result.get("subpath_timings") or {}).items():
            self._subpath_timings[key] = round(
                self._subpath_timings.get(key, 0.0) + float(value), 4,
            )
        self._subpath_timings["parallel_retrieval_ms"] = round(
            self._subpath_timings.get("parallel_retrieval_ms", 0.0) + elapsed, 2,
        )
        self._failed_paths.extend(result.get("failed_paths") or [])

    def _step_fusion_rerank(self, context: dict[str, Any]) -> None:
        """Step 5: Fusion + Rerank (T058: consumes the round recall output).

        With a wired pipeline, the round's fused/reranked candidates become
        machine state and re-enter evidence analysis (FR-014). Supplementary
        rounds accumulate so later rounds see earlier evidence too.
        """
        self._record_step("fusion_rerank")
        if self._round_recall is not None:
            self._round_candidates.append(self._round_recall.get("candidates", []))
            self._round_recall = None

    def _step_evidence_analysis(self, context: dict[str, Any]) -> None:
        """Step 6: Evidence analysis (blueprint sec 12, wired in T029).

        Calls the EvidenceAnalystAgent to produce a structured judgment.
        Stores the output in the state envelope and records judgment IDs.
        """
        self._record_step("evidence_analysis")
        if self._evidence_analyst is not None:
            # Evidence accumulated so far (T058): candidates carry excerpts so
            # the analyst judges real coverage; sub_problems give the hop map.
            # T064: excerpts pass the injection detector first — suspicious
            # content is marked (auditable) and high-risk fragments are
            # quarantined out of the internal prompt (FR-020).
            machine_candidates = self.get_candidates()
            evidence_view = self._build_evidence_view(machine_candidates)
            analyst_context = {
                **context,
                "run_id": self._run_id,
                "round_index": self._rounds_completed - 1,
                "sub_problems": self._planner_output.get("sub_problems", []),
                "evidence": evidence_view,
            }
            t0 = time.monotonic()
            result = self._evidence_analyst.run(analyst_context)
            self._record_agent_timing("evidence_analyst", t0)
            judgment = result.output
            self._latest_judgment = judgment
            judgment_id = judgment.get("judgment_id", "")
            if judgment_id:
                self._judgment_ids.append(judgment_id)
            self._envelope.set_agent_output("evidence_analyst", {
                "judgment_ids": list(self._judgment_ids),
                "schema_valid_all": judgment.get("schema_valid", True),
            })

    def _step_loop_decision(self, context: dict[str, Any], force_gap: bool) -> bool:
        """Step 7: Supplementary loop decision (deterministic controller, Constitution VI).

        The controller (not the Agent) decides whether to continue the
        supplementary retrieval loop. needs_supplementary is an Agent
        judgment INPUT, not an exclusive jump authority.
        """
        self._record_step("loop_decision")
        # Use the analyst judgment if available, else fall back to force_gap.
        # T069 / US2-AC3: a VERIFIABLE gap is required to continue —
        # needs_supplementary, an uncovered coverage state, or explicit
        # uncovered sub-problem ids. A bare "partial" coverage with no
        # explicit gap signal stops the loop (no wasted LLM round).
        if self._latest_judgment is not None:
            judgment = self._latest_judgment
            needs_supp = bool(judgment.get("needs_supplementary", False))
            uncovered_ids = judgment.get("uncovered_sub_problem_ids") or []
            has_gap = (
                needs_supp
                or judgment.get("coverage_state") == "uncovered"
                or bool(uncovered_ids)
            )
        else:
            has_gap = force_gap
        should_continue = has_gap and self._rounds_completed < self._max_rounds
        decision = {
            "round": self._rounds_completed,
            "should_continue": should_continue,
            "reason": "gap_detected" if should_continue else "no_gap_or_max_reached",
        }
        self._controller_decisions.append(decision)
        return should_continue

    def _step_context_orchestration(self, context: dict[str, Any]) -> None:
        """Step 8: Context orchestration (blueprint sec 12, wired in T037).

        Calls the ContextOrchestratorAgent to deduplicate, preserve diversity,
        and bin the final context. Stores the output in the state envelope.
        """
        self._record_step("context_orchestration")
        if self._context_orchestrator is not None:
            # Machine-recalled candidates win when present (T058); the
            # context-supplied list remains the fallback for stub wiring.
            machine_candidates = self.get_candidates()
            candidates = machine_candidates or context.get("candidates", [])
            orch_context = {
                **context,
                "candidates": candidates,
                "top_k": self._top_k_max,
            }
            t0 = time.monotonic()
            result = self._context_orchestrator.run(orch_context)
            self._record_agent_timing("context_orchestrator", t0)
            output = result.output
            # Parent-context supplementation within the boxing cap (T065,
            # FR-017/US3-AC2): deterministic controller owns the assembly.
            self._supplement_parent_context(context, output)
            self._envelope.set_agent_output("context_orchestrator", output)
        else:
            self._envelope.set_agent_output("context_orchestrator", {
                "context_result_id": "",
                "selection_list": [],
                "schema_valid": True,
            })

    def _step_response_serialization(self, context: dict[str, Any]) -> None:
        """Step 9: MCP response serialization (blueprint sec 12, reuses 001 bridge)."""
        self._record_step("response_serialization")
        # Determine terminal status from the latest evidence analysis
        if self._latest_judgment is not None:
            coverage = self._latest_judgment.get("coverage_state", "uncovered")
            conflict = self._latest_judgment.get("conflict_type", "none")
            needs_supp = self._latest_judgment.get("needs_supplementary", False)
            machine_candidates = self.get_candidates()
            if machine_candidates:
                has_evidence = True
            else:
                has_evidence = len(self._retrieval_queries) > 0 or coverage != "uncovered"
            max_rounds_reached = self._rounds_completed >= self._max_rounds
            self._completion_status = self.determine_terminal_status(
                coverage_state=coverage,
                conflict_type=conflict,
                has_evidence=has_evidence,
                has_gap=needs_supp,
                max_rounds_reached=max_rounds_reached,
            )
        else:
            self._completion_status = "complete"

    def determine_terminal_status(
        self,
        coverage_state: str = "uncovered",
        conflict_type: str = "none",
        has_evidence: bool = False,
        has_gap: bool = False,
        max_rounds_reached: bool = False,
        agent_failed: bool = False,
    ) -> str:
        """Determine the final completion_status (FR-015/FR-016/SC-011).

        Four-state decision (blueprint sec 14):
          - complete: full coverage, no unresolved conflict
          - partial: has reliable evidence but has gaps/conflict/failed paths
          - no_evidence: normal execution but no reliable evidence
          - failed: system error, cannot form valid response

        Constitution III: gaps are exposed, NOT filled with generated content.
        """
        # System failure takes priority
        if agent_failed:
            return "failed"

        # No evidence at all
        if not has_evidence and coverage_state == "uncovered":
            return "no_evidence"

        # Full coverage
        if coverage_state == "covered" and not has_gap:
            return "complete"

        # Has evidence but gaps/conflict -> partial (FR-016)
        # Gaps are exposed, not filled (Constitution III)
        if has_evidence and (has_gap or coverage_state == "partial" or conflict_type != "none"):
            return "partial"

        # Max rounds reached with gaps -> partial
        if max_rounds_reached and has_gap:
            return "partial"

        # Default: no evidence
        if not has_evidence:
            return "no_evidence"

        return "complete"
