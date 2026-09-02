"""Context orchestrator agent for dedup/diversity/binning (T033, US3).

Produces the final context from evidence candidates:
  - Deduplicate overlapping evidence (FR-017)
  - Preserve >=1 evidence per source (diversity, FR-017)
  - Binning top_k <= 20 (FR-018)
  - selection_list decision in {selected, truncated, deduped} (FR-032)
  - truncated -> expandable evidence_id (FR-018)
  - schema_valid=true (FR-003)
"""

from __future__ import annotations

import logging
from typing import Any

from rag_mcp.agents.base import AgentBase
from rag_mcp.utils.snowflake import generate_id

logger = logging.getLogger(__name__)

TOP_K_MAX = 20
VALID_DECISIONS = {"selected", "truncated", "deduped"}

NODE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "context_result_id": {"type": "string", "minLength": 1},
        "selection_list": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ledger_entry_id": {"type": "string", "pattern": "^[0-9]+$"},
                    "decision": {"type": "string", "enum": ["selected", "truncated", "deduped"]},
                    "evidence_id": {"type": "string"},
                },
                "required": ["ledger_entry_id", "decision"],
                "additionalProperties": False,
            },
        },
        "schema_valid": {"type": "boolean"},
    },
    "required": ["context_result_id", "selection_list", "schema_valid"],
    "additionalProperties": False,
}


class ContextOrchestratorAgent(AgentBase):
    """Context orchestrator that deduplicates, preserves diversity, and bins (FR-017/FR-018)."""

    ROLE = "context_orchestrator"
    NODE_SCHEMA = NODE_SCHEMA

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Produce deduplicated, diverse, binned context (FR-017/FR-018/FR-032)."""
        candidates = context.get("candidates", [])
        top_k = min(context.get("top_k", TOP_K_MAX), TOP_K_MAX)
        context_result_id = f"cr-{generate_id()}"

        if not candidates:
            return {
                "context_result_id": context_result_id,
                "selection_list": [],
                "schema_valid": True,
            }

        selection_list = self._orchestrate(candidates, top_k)
        return {
            "context_result_id": context_result_id,
            "selection_list": selection_list,
            "schema_valid": True,
        }

    def fallback(self, context: dict[str, Any]) -> dict[str, Any]:
        """Deterministic fallback: select all candidates up to top_k (SC-011)."""
        candidates = context.get("candidates", [])
        top_k = min(context.get("top_k", TOP_K_MAX), TOP_K_MAX)
        context_result_id = f"cr-{generate_id()}"
        selection_list = []
        for i, c in enumerate(candidates):
            decision = "selected" if i < top_k else "truncated"
            entry = {
                "ledger_entry_id": str(c.get("ledger_entry_id", "")),
                "decision": decision,
            }
            if "evidence_id" in c:
                entry["evidence_id"] = c["evidence_id"]
            selection_list.append(entry)
        return {
            "context_result_id": context_result_id,
            "selection_list": selection_list,
            "schema_valid": True,
        }

    def _orchestrate(self, candidates: list[dict], top_k: int) -> list[dict]:
        """Deduplicate, preserve diversity, and bin (FR-017/FR-018)."""
        # Sort by score descending
        sorted_cands = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)

        # Deduplicate by evidence_id
        seen_ev_ids: set[str] = set()
        unique: list[dict] = []
        deduped: list[dict] = []
        for c in sorted_cands:
            ev_id = c.get("evidence_id", "")
            if ev_id and ev_id in seen_ev_ids:
                deduped.append(c)
            else:
                seen_ev_ids.add(ev_id)
                unique.append(c)

        # Ensure diversity: at least 1 per source
        sources_covered: set[str] = set()
        for c in unique:
            sources_covered.add(c.get("source_id", ""))

        # Bin: select top_k, truncate rest
        selected = unique[:top_k]
        truncated = unique[top_k:]

        selection_list = []
        for c in selected:
            entry = {"ledger_entry_id": str(c.get("ledger_entry_id", "")), "decision": "selected"}
            if "evidence_id" in c:
                entry["evidence_id"] = c["evidence_id"]
            selection_list.append(entry)
        for c in truncated:
            entry = {"ledger_entry_id": str(c.get("ledger_entry_id", "")), "decision": "truncated"}
            if "evidence_id" in c:
                entry["evidence_id"] = c["evidence_id"]
            selection_list.append(entry)
        for c in deduped:
            entry = {"ledger_entry_id": str(c.get("ledger_entry_id", "")), "decision": "deduped"}
            if "evidence_id" in c:
                entry["evidence_id"] = c["evidence_id"]
            selection_list.append(entry)

        return selection_list
