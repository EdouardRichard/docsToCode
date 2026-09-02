"""Append-only evidence ledger store (T011, FR-008/FR-009/FR-022/FR-032).

Provides the EvidenceLedgerStore that manages append-only INSERT operations
for the evidence_ledger_entry table. No UPDATE or DELETE path exists (SC-006).

Key invariants:
  - Only INSERT (append-only, FR-008)
  - ledger_entry_id is a Snowflake ID (^[0-9]+$ string form, FR-032)
  - round_index / sub_problem_id are monotonic within a run (FR-009)
  - (request_id, evidence_id) bridge key resolves entries (FR-024)
  - Cross-scope writes are rejected (FR-022, Constitution hard constraint)
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.orchestration.models import EvidenceLedgerEntry
from rag_mcp.utils.snowflake import generate_id

logger = logging.getLogger(__name__)


class EvidenceLedgerStore:
    """Append-only evidence ledger store (FR-008/FR-009/FR-022).

    All writes are INSERT-only. The store validates the isolation triple
    (knowledge_scope_id, project_id, index_version) against the request
    project_scope before inserting, rejecting cross-scope writes.

    This class intentionally does NOT expose update/delete methods.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._round_counter: int = 0
        self._sub_problem_counter: int = 0

    def generate_ledger_entry_id(self) -> str:
        """Generate a Snowflake ID for a ledger entry (^[0-9]+$ string, FR-032)."""
        return str(generate_id())

    def generate_run_id(self) -> str:
        """Generate a Snowflake ID for an agentic run (^[0-9]+$ string)."""
        return str(generate_id())

    def next_round_index(self) -> int:
        """Return the next round_index (starts at 0, monotonic, FR-009)."""
        idx = self._round_counter
        self._round_counter += 1
        return idx

    def next_sub_problem_id(self) -> int:
        """Return the next sub_problem_id (starts at 1, monotonic, FR-009)."""
        self._sub_problem_counter += 1
        return self._sub_problem_counter

    def validate_scope(
        self,
        entry_data: dict[str, Any],
        project_scope: list[dict[str, Any]] | None,
    ) -> bool:
        """Validate that the entry isolation triple matches the request scope.

        Rejects cross-scope writes (FR-022, Constitution hard constraint).
        Also rejects empty/None project_scope (FR-021, no implicit scope).
        """
        if not project_scope:
            return False

        entry_scope_id = entry_data.get("knowledge_scope_id")
        entry_project_id = entry_data.get("project_id")
        entry_index_version = entry_data.get("index_version")

        for scope in project_scope:
            if (
                scope.get("knowledge_scope_id") == entry_scope_id
                and scope.get("project_id") == entry_project_id
                and scope.get("index_version") == entry_index_version
            ):
                return True
        return False

    async def insert_entry(
        self,
        entry_data: dict[str, Any],
        project_scope: list[dict[str, Any]] | None = None,
    ) -> EvidenceLedgerEntry:
        """Insert a new ledger entry (append-only, FR-008).

        Validates the isolation triple before inserting. Rejects cross-scope
        writes with a ValueError (FR-022).
        """
        if project_scope is not None:
            if not self.validate_scope(entry_data, project_scope):
                raise ValueError(
                    f"Cross-scope write rejected (FR-022): entry scope "
                    f"({entry_data.get('knowledge_scope_id')}, "
                    f"{entry_data.get('project_id')}, "
                    f"{entry_data.get('index_version')}) does not match "
                    f"request project_scope"
                )

        ledger_entry_id = entry_data.get("ledger_entry_id") or self.generate_ledger_entry_id()
        if isinstance(ledger_entry_id, int):
            ledger_entry_id = str(ledger_entry_id)

        entry = EvidenceLedgerEntry(
            ledger_entry_id=int(ledger_entry_id),
            request_id=entry_data["request_id"],
            run_id=entry_data["run_id"],
            round_index=entry_data["round_index"],
            sub_problem_id=entry_data["sub_problem_id"],
            evidence_id=entry_data["evidence_id"],
            retrieval_query=entry_data["retrieval_query"],
            retriever=entry_data["retriever"],
            score=entry_data["score"],
            source_version=entry_data["source_version"],
            source_position=entry_data["source_position"],
            knowledge_scope_id=entry_data["knowledge_scope_id"],
            knowledge_scope_type=entry_data["knowledge_scope_type"],
            project_id=entry_data["project_id"],
            index_version=entry_data["index_version"],
            referenced_by_agent=entry_data["referenced_by_agent"],
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def get_by_request_evidence(
        self,
        request_id: str,
        evidence_id: str,
    ) -> list[EvidenceLedgerEntry]:
        """Resolve ledger entries by the (request_id, evidence_id) bridge key.

        This is the external-facing bridge: ops/evaluation can resolve internal
        ledger entries using the output request_id + evidence_id (FR-024),
        without needing ledger-specific fields in the MCP output.
        """
        result = await self._session.execute(
            select(EvidenceLedgerEntry).where(
                EvidenceLedgerEntry.request_id == request_id,
                EvidenceLedgerEntry.evidence_id == evidence_id,
            )
        )
        return list(result.scalars().all())
