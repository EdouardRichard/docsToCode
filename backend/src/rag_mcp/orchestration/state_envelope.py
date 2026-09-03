"""State envelope for agentic_retrieval_run record (T017, FR-010/FR-031).

Creates and manages the agentic_retrieval_run record, ensuring fields
conform to agentic-retrieval-run.schema.json (blueprint sec 13/12).

The state envelope is the public state wrapper that carries:
  - project_scope (Constitution I, explicit scope)
  - completion_status (blueprint sec 14, four-state)
  - guardrail_state (FR-006)
  - rounds_completed (FR-005/FR-014)
  - agent_outputs_ref (blueprint sec 11/20)
  - ledger_ref (blueprint sec 13)
  - schema_valid_all (SC-011)
"""

from __future__ import annotations

from typing import Any


class StateEnvelope:
    """Manages the agentic_retrieval_run record (FR-010/FR-031).

    Provides incremental mutators for setting fields during the run,
    and to_dict() that returns a record conforming to
    agentic-retrieval-run.schema.json.
    """

    def __init__(
        self,
        run_id: str,
        request_id: str,
        project_scope: list[str],
        knowledge_scope_ids: list[str],
        task_context: dict[str, Any] | None = None,
    ) -> None:
        self._run_id = run_id
        self._request_id = request_id
        self._project_scope = project_scope
        self._knowledge_scope_ids = knowledge_scope_ids
        self._task_context = task_context
        self._completion_status: str = "failed"
        self._max_rounds: int = 2
        self._rounds_completed: int = 0
        self._guardrail_state: dict[str, Any] = {}
        self._sub_path_timings: dict[str, float] = {}
        self._agent_outputs_ref: dict[str, dict[str, Any]] = {
            "query_planner": {"sub_problems": [], "schema_valid": True},
            "evidence_analyst": {"judgment_ids": [], "schema_valid_all": True},
            "context_orchestrator": {
                "context_result_id": "",
                "selection_list": [],
                "schema_valid": True,
            },
        }
        self._ledger_ref: dict[str, Any] = {"ledger_entry_ids": [], "rounds": []}
        self._total_cost: float | None = None
        self._total_llm_tokens: float | None = None
        self._schema_valid_all: bool = True
        self._run_config: dict[str, Any] = {}

    def set_completion_status(self, status: str) -> None:
        self._completion_status = status

    def set_max_rounds(self, n: int) -> None:
        self._max_rounds = n

    def set_rounds_completed(self, n: int) -> None:
        self._rounds_completed = n

    def set_guardrail_state(self, state: dict[str, Any]) -> None:
        self._guardrail_state = state

    def set_sub_path_timings(self, timings: dict[str, float]) -> None:
        self._sub_path_timings = timings

    def set_agent_output(self, role: str, output: dict[str, Any]) -> None:
        self._agent_outputs_ref[role] = output

    def set_ledger_ref(self, ref: dict[str, Any]) -> None:
        self._ledger_ref = ref

    def set_schema_valid_all(self, valid: bool) -> None:
        self._schema_valid_all = valid

    def set_run_config(self, config: dict[str, Any]) -> None:
        self._run_config = config

    def set_total_cost(self, cost: float | None) -> None:
        self._total_cost = cost

    def set_total_llm_tokens(self, tokens: float | None) -> None:
        self._total_llm_tokens = tokens

    def to_dict(self) -> dict[str, Any]:
        """Return the full run record conforming to agentic-retrieval-run.schema.json."""
        record: dict[str, Any] = {
            "run_id": self._run_id,
            "request_id": self._request_id,
            "project_scope": self._project_scope,
            "knowledge_scope_ids": self._knowledge_scope_ids,
            "run_config": self._run_config,
            "completion_status": self._completion_status,
            "max_rounds": self._max_rounds,
            "rounds_completed": self._rounds_completed,
            "guardrail_state": self._guardrail_state,
            "sub_path_timings": self._sub_path_timings,
            "agent_outputs_ref": self._agent_outputs_ref,
            "ledger_ref": self._ledger_ref,
            "schema_valid_all": self._schema_valid_all,
        }
        if self._task_context is not None:
            record["task_context"] = self._task_context
        if self._total_cost is not None:
            record["total_cost"] = self._total_cost
        if self._total_llm_tokens is not None:
            record["total_llm_tokens"] = self._total_llm_tokens
        return record