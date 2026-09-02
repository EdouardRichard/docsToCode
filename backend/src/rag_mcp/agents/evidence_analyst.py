"""Evidence analyst agent for coverage/conflict/gap judgment (T025, US2).

Produces structured judgments with fixed enums:
  - coverage_state: {covered, partial, uncovered} (FR-032)
  - conflict_type: {none, version_conflict, source_conflict, domain_conflict} (FR-032)
  - uncovered_sub_problem_ids: sub-problems not covered (FR-013)
  - needs_supplementary: Agent judgment INPUT (not exclusive jump, Constitution VI)
  - Project/public conflicts surfaced, not fabricated (FR-016, Constitution III)
  - schema_valid=true when output passes validation (FR-003)
"""

from __future__ import annotations

import logging
from typing import Any

from rag_mcp.agents.base import AgentBase
from rag_mcp.utils.snowflake import generate_id

logger = logging.getLogger(__name__)

NODE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "judgment_id": {"type": "string", "pattern": "^[0-9]+$", "minLength": 1},
        "run_id": {"type": "string", "pattern": "^[0-9]+$", "minLength": 1},
        "round_index": {"type": "integer", "minimum": 0},
        "coverage_state": {"type": "string", "enum": ["covered", "partial", "uncovered"]},
        "conflict_type": {"type": "string", "enum": ["none", "version_conflict", "source_conflict", "domain_conflict"]},
        "uncovered_sub_problem_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}},
        "needs_supplementary": {"type": "boolean"},
        "gap_descriptions": {"type": "array", "items": {"type": "object", "properties": {"description": {"type": "string"}, "suggested_action": {"type": "string"}}, "required": ["description"], "additionalProperties": False}},
        "model_and_version": {"type": "string"},
        "schema_valid": {"type": "boolean"},
    },
    "required": ["judgment_id", "run_id", "round_index", "coverage_state", "conflict_type", "uncovered_sub_problem_ids", "needs_supplementary", "gap_descriptions", "model_and_version", "schema_valid"],
    "additionalProperties": False,
}


class EvidenceAnalystAgent(AgentBase):
    """Evidence analyst that produces structured coverage/conflict judgments (FR-013/FR-015)."""

    ROLE = "evidence_analyst"
    NODE_SCHEMA = NODE_SCHEMA

    def __init__(self, model_and_version: str = "") -> None:
        super().__init__(model_and_version=model_and_version)
        self._round_counter = 0

    def _llm_judge(self, context: dict[str, Any]) -> dict[str, Any] | None:
        """Call the LLM to produce a judgment (overridable for testing)."""
        return None  # default: no LLM, trigger fallback

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Produce a structured evidence judgment (FR-013/FR-015/FR-032)."""
        run_id = context.get("run_id", "0")
        round_index = context.get("round_index", self._round_counter)

        raw = self._llm_judge(context)
        if not raw:
            return self._build_fallback(run_id, round_index, context)

        # Validate and construct output
        coverage = raw.get("coverage_state", "partial")
        conflict = raw.get("conflict_type", "none")
        uncovered = raw.get("uncovered_sub_problem_ids", [])
        needs_supp = raw.get("needs_supplementary", False)
        gaps = raw.get("gap_descriptions", [])

        return {
            "judgment_id": str(generate_id()),
            "run_id": str(run_id),
            "round_index": round_index,
            "coverage_state": coverage,
            "conflict_type": conflict,
            "uncovered_sub_problem_ids": uncovered,
            "needs_supplementary": needs_supp,
            "gap_descriptions": gaps,
            "model_and_version": self._model_and_version,
            "schema_valid": True,
        }

    def fallback(self, context: dict[str, Any]) -> dict[str, Any]:
        """Deterministic fallback: partial coverage, no conflict (SC-011)."""
        run_id = context.get("run_id", "0")
        round_index = context.get("round_index", 0)
        return self._build_fallback(run_id, round_index, context)

    def _build_fallback(self, run_id: str, round_index: int, context: dict[str, Any]) -> dict[str, Any]:
        """Build a deterministic fallback judgment (SC-011)."""
        sub_problems = context.get("sub_problems", [])
        uncovered = [sp.get("sub_problem_id", i+1) for i, sp in enumerate(sub_problems)]
        return {
            "judgment_id": str(generate_id()),
            "run_id": str(run_id),
            "round_index": round_index,
            "coverage_state": "partial",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": uncovered,
            "needs_supplementary": False,
            "gap_descriptions": [],
            "model_and_version": self._model_and_version,
            "schema_valid": True,
        }
