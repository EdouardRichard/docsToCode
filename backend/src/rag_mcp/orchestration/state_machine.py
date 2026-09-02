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

import logging
from typing import Any

from rag_mcp.config import get_settings
from rag_mcp.orchestration.state_envelope import StateEnvelope

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
        self._retrieval_queries: list[str] = []
        self._latest_judgment: dict[str, Any] | None = None
        self._judgment_ids: list[str] = []

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

    def get_retrieval_queries(self) -> list[str]:
        """Return the sub-problem queries for step 4 parallel retrieval (blueprint sec 12)."""
        return list(self._retrieval_queries)

    def run(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute the nine-step main flow with bounded supplementary loop.

        The deterministic controller (not the Agent) decides whether to
        loop back for supplementary retrieval (Constitution VI).
        """
        context = context or {}

        # Step 1: receive and validate
        self._step_receive_validate(context)

        # Step 2: resolve scope
        self._step_resolve_scope(context)

        # Rounds 0..max_rounds
        force_gap = context.get("force_gap", False)
        while True:
            self._rounds_completed += 1

            # Step 3: query planning
            self._step_query_planning(context)
            # Step 4: parallel retrieval
            self._step_parallel_retrieval(context)
            # Step 5: fusion + rerank
            self._step_fusion_rerank(context)
            # Step 6: evidence analysis
            self._step_evidence_analysis(context)
            # Step 7: loop decision (deterministic controller)
            should_continue = self._step_loop_decision(context, force_gap)

            if not should_continue:
                break

            if self._rounds_completed >= self._max_rounds:
                break

        # Step 8: context orchestration
        self._step_context_orchestration(context)
        # Step 9: response serialization
        self._step_response_serialization(context)

        # Finalize state envelope
        self._envelope.set_rounds_completed(self._rounds_completed)
        self._envelope.set_completion_status(self._completion_status)

        return self._envelope.to_dict()

    def _record_step(self, name: str) -> None:
        self._executed_steps.append(name)

    def _step_receive_validate(self, context: dict[str, Any]) -> None:
        """Step 1: Receive and validate request (project_scope required, FR-021)."""
        self._record_step("receive_validate")
        if not self._project_scope:
            raise ValueError("project_scope is required (FR-021, Constitution I)")

    def _step_resolve_scope(self, context: dict[str, Any]) -> None:
        """Step 2: Resolve knowledge scope from project_scope."""
        self._record_step("resolve_scope")

    def _step_query_planning(self, context: dict[str, Any]) -> None:
        """Step 3: Query planning (blueprint sec 12, wired in T023).

        Calls the QueryPlannerAgent to decompose the query into sub-problems.
        Stores the output in the state envelope and extracts sub-problem
        queries for step 4 parallel retrieval.
        """
        self._record_step("query_planning")
        if self._query_planner is not None:
            result = self._query_planner.run(context)
            output = result.output
            # Store in state envelope
            self._envelope.set_agent_output("query_planner", output)
            # Extract sub-problem queries for step 4
            self._retrieval_queries = [
                sp.get("query", "") for sp in output.get("sub_problems", [])
            ]
        else:
            # No planner wired: use original query as single sub-problem
            query = context.get("query", "")
            self._retrieval_queries = [query] if query else []
            self._envelope.set_agent_output("query_planner", {
                "sub_problems": [{"sub_problem_id": 1, "query": query, "signals": ["dense"]}] if query else [],
                "schema_valid": True,
            })

    def _step_parallel_retrieval(self, context: dict[str, Any]) -> None:
        """Step 4: Parallel retrieval (stub - reuses 002/004)."""
        self._record_step("parallel_retrieval")

    def _step_fusion_rerank(self, context: dict[str, Any]) -> None:
        """Step 5: Fusion + Rerank (stub - reuses 002)."""
        self._record_step("fusion_rerank")

    def _step_evidence_analysis(self, context: dict[str, Any]) -> None:
        """Step 6: Evidence analysis (blueprint sec 12, wired in T029).

        Calls the EvidenceAnalystAgent to produce a structured judgment.
        Stores the output in the state envelope and records judgment IDs.
        """
        self._record_step("evidence_analysis")
        if self._evidence_analyst is not None:
            analyst_context = {
                **context,
                "run_id": self._run_id,
                "round_index": self._rounds_completed - 1,
            }
            result = self._evidence_analyst.run(analyst_context)
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
        # Use the analyst judgment if available, else fall back to force_gap
        if self._latest_judgment is not None:
            needs_supp = self._latest_judgment.get("needs_supplementary", False)
            has_gap = (
                self._latest_judgment.get("coverage_state") in ("partial", "uncovered")
                or needs_supp
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
        """Step 8: Context orchestration (stub - wired in T033/T037)."""
        self._record_step("context_orchestration")

    def _step_response_serialization(self, context: dict[str, Any]) -> None:
        """Step 9: MCP response serialization (blueprint sec 12, reuses 001 bridge)."""
        self._record_step("response_serialization")
        # Determine terminal status from the latest evidence analysis
        if self._latest_judgment is not None:
            coverage = self._latest_judgment.get("coverage_state", "uncovered")
            conflict = self._latest_judgment.get("conflict_type", "none")
            needs_supp = self._latest_judgment.get("needs_supplementary", False)
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
