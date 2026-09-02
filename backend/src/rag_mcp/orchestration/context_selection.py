"""Context selection list store (T035, US3).

Append-only store for context_selection_list records.
Only INSERT (no UPDATE/DELETE, FR-008/FR-017).
Records context_result_id + decision enum (FR-032).
Does NOT overwrite original ledger entries (FR-008).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.orchestration.models import ContextSelectionList

logger = logging.getLogger(__name__)

VALID_DECISIONS = {"selected", "truncated", "deduped"}


class ContextSelectionStore:
    """Append-only store for context selection list entries (FR-008/FR-017)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def to_selection_record(self, data: dict[str, Any]) -> dict[str, Any]:
        """Convert selection data to a schema-conforming record."""
        decision = data.get("decision", "")
        if decision not in VALID_DECISIONS:
            raise ValueError(f"Invalid decision: {decision}. Must be one of {VALID_DECISIONS}")
        ledger_entry_id = data.get("ledger_entry_id", "")
        if isinstance(ledger_entry_id, int):
            ledger_entry_id = str(ledger_entry_id)
        return {
            "context_result_id": data["context_result_id"],
            "run_id": str(data.get("run_id", "")),
            "ledger_entry_id": ledger_entry_id,
            "decision": decision,
        }

    async def insert_selection(self, data: dict[str, Any]) -> ContextSelectionList:
        """Insert a selection entry (append-only, FR-008/FR-017)."""
        record = self.to_selection_record(data)
        entry = ContextSelectionList(
            context_result_id=record["context_result_id"],
            run_id=record["run_id"],
            ledger_entry_id=int(record["ledger_entry_id"]),
            decision=record["decision"],
        )
        self._session.add(entry)
        await self._session.flush()
        return entry
